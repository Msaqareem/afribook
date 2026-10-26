from tests.helpers import PASSWORD, PHONE, PHONE_E164, login, make_user, outbox


def test_login_success_redirects_to_dashboard(client, app):
    make_user(app)
    resp = login(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200


def test_login_failures_are_indistinguishable(client, app):
    make_user(app)
    wrong_pw = login(client, password="wrong-password-1")
    unknown = login(client, phone="0799999999")
    assert wrong_pw.status_code == unknown.status_code == 401
    assert b"Invalid phone number or password" in wrong_pw.data
    assert b"Invalid phone number or password" in unknown.data


def test_inactive_user_cannot_login(client, app):
    make_user(app, active=False)
    assert login(client).status_code == 401


def test_unverified_user_is_sent_to_verification(client, app):
    make_user(app, verified=False)
    resp = login(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/verify-phone")
    assert outbox(app)[-1][0] == PHONE_E164


def test_logout_requires_post_and_ends_session(client, app):
    make_user(app)
    login(client)
    assert client.get("/logout").status_code == 405
    client.post("/logout")
    resp = client.get("/dashboard")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_open_redirect_is_blocked(client, app):
    make_user(app)
    resp = client.post("/login?next=//evil.com", data={"phone": PHONE, "password": PASSWORD, "next": "//evil.com"})
    assert resp.headers["Location"].endswith("/dashboard")


def test_safe_next_is_followed(client, app):
    make_user(app)
    resp = client.post("/login", data={"phone": PHONE, "password": PASSWORD, "next": "/dashboard"})
    assert resp.headers["Location"].endswith("/dashboard")


def test_anonymous_is_redirected_to_login(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    assert client.get("/admin/").status_code == 302


def test_owner_cannot_access_admin(client, app):
    make_user(app)
    login(client)
    assert client.get("/admin/").status_code == 403


def test_admin_access_and_owner_area_blocked(client, app):
    make_user(app, role="admin")
    resp = login(client)
    assert resp.headers["Location"].endswith("/admin/")
    assert client.get("/admin/").status_code == 200
    assert client.get("/dashboard").status_code == 403


def test_public_signup_cannot_create_admin(client, app):
    client.post("/register", data={"phone": PHONE, "password": PASSWORD, "confirm_password": PASSWORD,
                                   "role": "admin"})
    from tests.helpers import get_user
    assert get_user(app).role == "owner"
