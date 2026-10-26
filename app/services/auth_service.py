"""Authentication business logic: registration, OTP, login, password reset.

Routes stay thin and call these functions. Transaction notes:
 * check_otp() commits failed attempts itself; on success it leaves the
   transaction open so the caller applies its change and commits once.
"""
import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from flask import current_app
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.integrations.sms import get_sms_provider
from app.models.otp import PURPOSE_RESET, PURPOSE_SIGNUP, PasswordResetOtp
from app.models.user import ROLE_ADMIN, ROLE_OWNER, User
from app.services.errors import ValidationError
from app.utils.phone import mask_phone, normalize_ke_phone
from app.utils.timeutil import utcnow

logger = logging.getLogger(__name__)

# Used to keep login timing similar for unknown phone numbers.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


class PhoneAlreadyRegistered(Exception):
    pass


@dataclass(frozen=True)
class AuthResult:
    status: str  # "ok" | "unverified" | "invalid"
    user: User | None = None


# --------------------------------------------------------------------- helpers
def hash_otp(code: str, phone: str, purpose: str, secret: str) -> str:
    """HMAC-SHA256 of the code, bound to phone and purpose."""
    message = f"{purpose}:{phone}:{code}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def validate_password(password: str) -> None:
    cfg = current_app.config
    if len(password or "") < cfg["PASSWORD_MIN_LENGTH"]:
        raise ValidationError(f"Password must be at least {cfg['PASSWORD_MIN_LENGTH']} characters.")
    if len(password) > cfg["PASSWORD_MAX_LENGTH"]:
        raise ValidationError(f"Password must be at most {cfg['PASSWORD_MAX_LENGTH']} characters.")


def _require_phone(raw) -> str:
    phone = normalize_ke_phone(raw)
    if phone is None:
        raise ValidationError("Enter a valid Kenyan mobile number, e.g. 0712 345 678.")
    return phone


def _get_user(phone: str) -> User | None:
    return db.session.scalar(select(User).where(User.phone_e164 == phone))


# ------------------------------------------------------------------------- OTP
def issue_otp(phone: str, purpose: str, user_id: int | None = None) -> bool:
    """Create and SMS a new code. Returns False if the hourly send cap is reached."""
    cfg = current_app.config
    now = utcnow()

    recent = db.session.scalar(
        select(func.count())
        .select_from(PasswordResetOtp)
        .where(
            PasswordResetOtp.phone_e164 == phone,
            PasswordResetOtp.purpose == purpose,
            PasswordResetOtp.created_at > now - timedelta(hours=1),
        )
    )
    if recent >= cfg["OTP_MAX_SENDS_PER_HOUR"]:
        logger.info("otp_send_capped phone=%s purpose=%s", mask_phone(phone), purpose)
        return False

    # A new code replaces any older live code.
    db.session.execute(
        update(PasswordResetOtp)
        .where(
            PasswordResetOtp.phone_e164 == phone,
            PasswordResetOtp.purpose == purpose,
            PasswordResetOtp.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )

    length = cfg["OTP_LENGTH"]
    code = f"{secrets.randbelow(10 ** length):0{length}d}"
    db.session.add(
        PasswordResetOtp(
            user_id=user_id,
            phone_e164=phone,
            purpose=purpose,
            code_hash=hash_otp(code, phone, purpose, cfg["SECRET_KEY"]),
            expires_at=now + timedelta(minutes=cfg["OTP_TTL_MINUTES"]),
        )
    )
    db.session.commit()

    what = "verification" if purpose == PURPOSE_SIGNUP else "password reset"
    message = (
        f"AFRIBOOK: your {what} code is {code}. "
        f"It expires in {cfg['OTP_TTL_MINUTES']} minutes. Do not share it with anyone."
    )
    result = get_sms_provider().send(phone, message)
    if not result.ok:
        logger.warning("otp_sms_failed phone=%s purpose=%s error=%s", mask_phone(phone), purpose, result.error)
    return True


def check_otp(phone: str, purpose: str, code: str) -> PasswordResetOtp | None:
    """Validate a code. Counts the attempt; consumes the code on success (uncommitted)."""
    cfg = current_app.config
    code = (code or "").strip()
    if len(code) != cfg["OTP_LENGTH"] or not code.isascii() or not code.isdigit():
        return None

    now = utcnow()
    row = db.session.scalar(
        select(PasswordResetOtp)
        .where(
            PasswordResetOtp.phone_e164 == phone,
            PasswordResetOtp.purpose == purpose,
            PasswordResetOtp.consumed_at.is_(None),
            PasswordResetOtp.expires_at > now,
        )
        .order_by(PasswordResetOtp.created_at.desc(), PasswordResetOtp.id.desc())
        .limit(1)
        .with_for_update()
    )
    if row is None:
        return None
    if row.attempts >= cfg["OTP_MAX_ATTEMPTS"]:
        db.session.rollback()
        return None

    row.attempts += 1
    expected = hash_otp(code, phone, purpose, cfg["SECRET_KEY"])
    if hmac.compare_digest(row.code_hash, expected):
        row.consumed_at = now  # caller commits
        return row

    db.session.commit()  # persist the failed attempt
    return None


# ---------------------------------------------------------------- registration
def register_owner(raw_phone: str, password: str) -> User:
    phone = _require_phone(raw_phone)
    validate_password(password)

    user = _get_user(phone)
    if user is not None:
        if user.phone_verified_at is not None or user.role != ROLE_OWNER:
            raise PhoneAlreadyRegistered()
        # Unverified sign-up: owns nothing yet, so the real owner may claim the number.
        user.set_password(password)
    else:
        user = User(phone_e164=phone, role=ROLE_OWNER)
        user.set_password(password)
        db.session.add(user)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise PhoneAlreadyRegistered() from None

    issue_otp(phone, PURPOSE_SIGNUP, user.id)
    return user


def resend_signup_otp(phone: str) -> bool:
    user = _get_user(phone)
    if user is None or user.phone_verified_at is not None or not user.is_active:
        return False
    return issue_otp(phone, PURPOSE_SIGNUP, user.id)


def verify_signup(phone: str, code: str) -> User | None:
    row = check_otp(phone, PURPOSE_SIGNUP, code)
    if row is None:
        return None
    user = db.session.scalar(select(User).where(User.phone_e164 == phone).with_for_update())
    if user is None or not user.is_active:
        db.session.rollback()
        return None
    if user.phone_verified_at is None:
        user.phone_verified_at = utcnow()
    db.session.commit()
    return user


def create_admin_user(raw_phone: str, password: str) -> User:
    """Used by the `flask create-admin` CLI only; admins are never created via signup."""
    phone = _require_phone(raw_phone)
    validate_password(password)
    if _get_user(phone) is not None:
        raise PhoneAlreadyRegistered()
    user = User(phone_e164=phone, role=ROLE_ADMIN, phone_verified_at=utcnow())
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise PhoneAlreadyRegistered() from None
    return user


# ----------------------------------------------------------------------- login
def authenticate(raw_phone: str, password: str) -> AuthResult:
    phone = normalize_ke_phone(raw_phone)
    user = _get_user(phone) if phone else None
    password = password or ""
    too_long = len(password) > current_app.config["PASSWORD_MAX_LENGTH"]
    password_ok = check_password_hash(user.password_hash if user else _DUMMY_HASH, "" if too_long else password)

    if user is None or not password_ok or too_long or not user.is_active:
        return AuthResult("invalid")
    if user.phone_verified_at is None:
        return AuthResult("unverified", user)
    return AuthResult("ok", user)


# -------------------------------------------------------------- password reset
def request_password_reset(raw_phone: str) -> None:
    """Send a reset code if the account exists. Never reveals whether it does."""
    phone = normalize_ke_phone(raw_phone)
    if phone is None:
        return
    user = _get_user(phone)
    if user is not None and user.is_active and user.phone_verified_at is not None:
        issue_otp(phone, PURPOSE_RESET, user.id)


def reset_password(raw_phone: str, code: str, new_password: str) -> bool:
    """Returns True if the password was changed. Raises ValidationError for a bad new password."""
    validate_password(new_password)
    phone = normalize_ke_phone(raw_phone)
    if phone is None:
        return False
    row = check_otp(phone, PURPOSE_RESET, code)
    if row is None:
        return False
    user = db.session.scalar(select(User).where(User.phone_e164 == phone).with_for_update())
    if user is None or not user.is_active:
        db.session.rollback()
        return False
    user.set_password(new_password)  # also invalidates existing sessions (see User.get_id)
    db.session.commit()
    logger.info("password_reset_ok user=%s", user.id)
    return True
