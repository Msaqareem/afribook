from tests.helpers import PASSWORD, PHONE, PHONE_E164, get_user, last_code, login, make_user, outbox

NEW_PASSWORD = "brand-new-pass-1"


def request_reset(client, phone=PHONE):
    return client.post("/password-reset", data={"phone": phone})


def confirm(client, code, new=NEW_PASSWORD, confirm_pw=None):
    return client.post("/password-reset/confirm", data={
        "code": code, "new_password": new, "confirm_password": confirm_pw or new})


def test_reset_response_does_not_reveal_account_existence(app):
    make_user(app)
    c1, c2 = app.test_client(), app.test_client()
    known = request_reset(c1, PHONE)
    unknown = request_reset(c2, "0799999999")
    assert known.status_code == unknown.status_code == 302
    assert known.headers["Location"] == unknown.headers["Location"]
    assert len(outbox(app)) == 1  # only the real account received an SMS
    assert outbox(app)[0][0] == PHONE_E164


def test_reset_for_unverified_account_sends_nothing(client, app):
    make_user(app, verified=False)
    request_reset(client)
    assert outbox(app) == []


def test_reset_changes_password(client, app):
    make_user(app)
    request_reset(client)
    resp = confirm(client, last_code(app))
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")
    assert get_user(app).check_password(NEW_PASSWORD)
    assert login(app.test_client(), password=PASSWORD).status_code == 401
    assert login(app.test_client(), password=NEW_PASSWORD).status_code == 302


def test_wrong_code_is_rejected_generically(client, app):
    make_user(app)
    request_reset(client)
    code = last_code(app)
    wrong = "000000" if code != "000000" else "111111"
    resp = confirm(client, wrong)
    assert resp.status_code == 400 and b"invalid or has expired" in resp.data
    assert get_user(app).check_password(PASSWORD)


def test_weak_new_password_does_not_burn_code(client, app):
    make_user(app)
    request_reset(client)
    code = last_code(app)
    assert confirm(client, code, new="short").status_code == 400
    assert confirm(client, code).status_code == 302


def test_reset_code_is_single_use(client, app):
    make_user(app)
    request_reset(client)
    code = last_code(app)
    confirm(client, code)
    with client.session_transaction() as sess:
        sess["pending_reset_phone"] = PHONE_E164
    assert confirm(client, code, new="another-pass-123").status_code == 400


def test_password_change_invalidates_existing_sessions(app):
    make_user(app)
    logged_in = app.test_client()
    login(logged_in)
    assert logged_in.get("/dashboard").status_code == 200

    resetter = app.test_client()
    request_reset(resetter)
    confirm(resetter, last_code(app))

    resp = logged_in.get("/dashboard")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_signup_code_cannot_reset_password(client, app):
    make_user(app, verified=False)
    client.post("/register", data={"phone": PHONE, "password": PASSWORD, "confirm_password": PASSWORD})
    signup_code = last_code(app)
    with client.session_transaction() as sess:
        sess["pending_reset_phone"] = PHONE_E164
    assert confirm(client, signup_code).status_code == 400
