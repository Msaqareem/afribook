from app.extensions import db
from tests.helpers import (PASSWORD, PHONE, PHONE_E164, expire_latest_otp, get_user, last_code,
                           make_user, otp_rows, outbox)


def register(client, phone=PHONE, password=PASSWORD, confirm=None):
    return client.post("/register", data={
        "phone": phone, "password": password,
        "confirm_password": password if confirm is None else confirm,
    })


def test_register_creates_unverified_owner_and_sends_otp(client, app):
    resp = register(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/verify-phone")
    user = get_user(app)
    assert user.role == "owner" and user.phone_verified_at is None
    assert user.password_hash != PASSWORD
    assert outbox(app)[-1][0] == PHONE_E164


def test_register_rejects_invalid_phone_short_password_and_mismatch(client):
    assert register(client, phone="12345").status_code == 400
    assert register(client, password="short").status_code == 400
    assert register(client, confirm="different-pass-1").status_code == 400


def test_register_duplicate_verified_phone_conflicts(client, app):
    make_user(app)
    assert register(client).status_code == 409


def test_unverified_signup_can_be_reclaimed(client, app):
    register(client, password="first-password-1")
    register(client, password="second-password-1")
    assert get_user(app).check_password("second-password-1")


def test_otp_is_stored_hashed(client, app):
    register(client)
    code = last_code(app)
    row = otp_rows(app)[-1]
    assert code not in row.code_hash and row.code_hash != code


def test_verify_with_correct_code_logs_in(client, app):
    register(client)
    resp = client.post("/verify-phone", data={"code": last_code(app)})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/dashboard")
    assert get_user(app).phone_verified_at is not None
    assert client.get("/dashboard").status_code == 200


def test_verify_wrong_code_fails(client, app):
    register(client)
    assert client.post("/verify-phone", data={"code": "000000"}).status_code == 400
    assert get_user(app).phone_verified_at is None


def test_verify_locks_out_after_max_attempts(client, app):
    register(client)
    good = last_code(app)
    wrong = "000000" if good != "000000" else "111111"
    for _ in range(app.config["OTP_MAX_ATTEMPTS"]):
        client.post("/verify-phone", data={"code": wrong})
    assert client.post("/verify-phone", data={"code": good}).status_code == 400
    assert get_user(app).phone_verified_at is None


def test_expired_code_fails(client, app):
    register(client)
    code = last_code(app)
    expire_latest_otp(app)
    assert client.post("/verify-phone", data={"code": code}).status_code == 400


def test_code_is_single_use(client, app):
    register(client)
    code = last_code(app)
    client.post("/verify-phone", data={"code": code})
    client.post("/logout")
    # fresh session pointing at the same pending phone
    with client.session_transaction() as sess:
        sess["pending_verify_phone"] = PHONE_E164
    assert client.post("/verify-phone", data={"code": code}).status_code == 400


def test_resend_replaces_old_code_and_is_capped(client, app):
    register(client)
    first = last_code(app)
    client.post("/verify-phone/resend")
    second = last_code(app)
    if first != second:
        assert client.post("/verify-phone", data={"code": first}).status_code == 400
    client.post("/verify-phone/resend")
    sent_before = len(outbox(app))
    client.post("/verify-phone/resend")  # 4th send within the hour: capped
    assert len(outbox(app)) == sent_before
    assert len(outbox(app)) == app.config["OTP_MAX_SENDS_PER_HOUR"]
