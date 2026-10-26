import re
from datetime import timedelta

from sqlalchemy import select

from app.extensions import db
from app.models.otp import PasswordResetOtp
from app.models.user import ROLE_OWNER, User
from app.utils.timeutil import utcnow

PHONE = "0712345678"
PHONE_E164 = "+254712345678"
PASSWORD = "correct-horse-1"


def make_user(app, phone=PHONE_E164, password=PASSWORD, role=ROLE_OWNER, verified=True, active=True):
    with app.app_context():
        user = User(phone_e164=phone, role=role, is_active=active,
                    phone_verified_at=utcnow() if verified else None)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user.id


def get_user(app, phone=PHONE_E164):
    with app.app_context():
        user = db.session.scalar(select(User).where(User.phone_e164 == phone))
        if user is not None:
            db.session.expunge(user)
        return user


def outbox(app):
    return app.extensions["sms_provider"].outbox


def last_code(app):
    """The 6-digit code from the most recent mock SMS."""
    to, message = outbox(app)[-1]
    return re.search(r"\b(\d{6})\b", message).group(1)


def otp_rows(app, phone=PHONE_E164):
    with app.app_context():
        rows = db.session.scalars(
            select(PasswordResetOtp).where(PasswordResetOtp.phone_e164 == phone).order_by(PasswordResetOtp.id)
        ).all()
        for row in rows:
            db.session.expunge(row)
        return rows


def expire_latest_otp(app, phone=PHONE_E164):
    with app.app_context():
        row = db.session.scalars(
            select(PasswordResetOtp).where(PasswordResetOtp.phone_e164 == phone).order_by(PasswordResetOtp.id.desc())
        ).first()
        row.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()


def login(client, phone=PHONE, password=PASSWORD):
    return client.post("/login", data={"phone": phone, "password": password})
