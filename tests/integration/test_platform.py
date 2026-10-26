import pytest
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models.user import User
from tests.helpers import PASSWORD, PHONE, make_user


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200 and resp.get_json() == {"status": "ok"}


def test_security_headers_present(client):
    resp = client.get("/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_404_page_has_no_stack_trace(client):
    resp = client.get("/no-such-page")
    assert resp.status_code == 404 and b"Traceback" not in resp.data


def test_csrf_blocks_posts_without_token(_schema):
    app = create_app("testing", overrides={"WTF_CSRF_ENABLED": True})
    resp = app.test_client().post("/login", data={"phone": PHONE, "password": PASSWORD})
    assert resp.status_code == 400


def test_login_is_rate_limited_per_phone(_schema):
    app = create_app("testing", overrides={"RATELIMIT_ENABLED": True})
    client = app.test_client()
    codes = [client.post("/login", data={"phone": PHONE, "password": "wrong-password-1"}).status_code
             for _ in range(6)]
    assert codes[:5] == [401] * 5
    assert codes[5] == 429


def test_create_admin_cli(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-admin", "--phone", "0733000111", "--password", "admin-pass-123"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        user = db.session.scalar(select(User).where(User.phone_e164 == "+254733000111"))
        assert user.role == "admin" and user.phone_verified_at is not None
    again = runner.invoke(args=["create-admin", "--phone", "0733000111", "--password", "admin-pass-123"])
    assert again.exit_code != 0


def test_unique_phone_enforced_by_database(app):
    from sqlalchemy.exc import IntegrityError
    make_user(app)
    with pytest.raises(IntegrityError):
        make_user(app)


def test_role_check_constraint(app):
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        make_user(app, phone="+254722000111", role="superuser")
