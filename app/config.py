"""Application configuration.

All secrets and environment-specific values come from environment variables
(see .env.example). Nothing in this file is a real credential.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _database_url(name: str):
    """Read a Postgres URL and make it use the psycopg (v3) driver.

    Supabase/Render give URLs starting with postgres:// or postgresql://.
    """
    url = os.environ.get(name)
    if not url:
        return None
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _database_url("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Sessions / cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    # CSRF (Flask-WTF)
    WTF_CSRF_TIME_LIMIT = 8 * 3600

    # Rate limiting (Flask-Limiter). memory:// is per-process; use Redis in production.
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # Number of reverse proxies in front of the app (Render = 1). 0 = none.
    PROXY_FIX_HOPS = _int("PROXY_FIX_HOPS", 0)

    # SMS
    SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "mock")
    MOCK_SMS_ECHO = False  # log full SMS text (incl. OTP) from the mock provider; dev only

    # OTP rules
    OTP_LENGTH = 6
    OTP_TTL_MINUTES = _int("OTP_TTL_MINUTES", 10)
    OTP_MAX_ATTEMPTS = _int("OTP_MAX_ATTEMPTS", 5)
    OTP_MAX_SENDS_PER_HOUR = _int("OTP_MAX_SENDS_PER_HOUR", 3)

    # Booking rules (Document 2 section 6.1). Notice and horizon are product defaults to confirm.
    BOOKING_SLOT_INCREMENT_MINUTES = _int("BOOKING_SLOT_INCREMENT_MINUTES", 15)
    BOOKING_MIN_NOTICE_MINUTES = _int("BOOKING_MIN_NOTICE_MINUTES", 30)
    BOOKING_MAX_ADVANCE_DAYS = _int("BOOKING_MAX_ADVANCE_DAYS", 60)
    BOOKING_HOLD_MINUTES = _int("BOOKING_HOLD_MINUTES", 5)
    BOOKING_MAX_HOLDS_PER_PHONE = _int("BOOKING_MAX_HOLDS_PER_PHONE", 3)

    # Payments (Bucket 6). See docs/payments.md.
    PAYMENT_PROVIDER = os.environ.get("PAYMENT_PROVIDER", "mock")          # mock | intasend
    ALLOW_MOCK_PAYMENT_PAGE = False                                          # /dev/mock-pay simulator (dev and tests only)
    PAYMENT_FEE_BPS = _int("PAYMENT_FEE_BPS", 150)                          # 150 = 1.5% (basis points)
    PAYMENT_GRACE_MINUTES = _int("PAYMENT_GRACE_MINUTES", 3)                # hold is stretched to at least this once STK is sent
    PAYMENT_RECONCILE_AFTER_SECONDS = _int("PAYMENT_RECONCILE_AFTER_SECONDS", 60)
    MOCK_PAYMENT_SECRET = os.environ.get("MOCK_PAYMENT_SECRET") or "dev-mock-payment-secret"
    INTASEND_SECRET_KEY = os.environ.get("INTASEND_SECRET_KEY", "")
    INTASEND_PUBLISHABLE_KEY = os.environ.get("INTASEND_PUBLISHABLE_KEY", "")
    INTASEND_TEST_MODE = _bool("INTASEND_TEST_MODE", True)                  # true = sandbox.intasend.com
    INTASEND_WEBHOOK_CHALLENGE = os.environ.get("INTASEND_WEBHOOK_CHALLENGE", "")

    # Passwords
    PASSWORD_MIN_LENGTH = 8
    PASSWORD_MAX_LENGTH = 128

    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    @classmethod
    def validate(cls, config: dict) -> None:
        """Fail fast on missing configuration."""
        if not config.get("SECRET_KEY"):
            raise RuntimeError("SECRET_KEY is not set.")
        if not config.get("SQLALCHEMY_DATABASE_URI"):
            raise RuntimeError("DATABASE_URL is not set (see .env.example).")


class DevelopmentConfig(Config):
    DEBUG = True
    # Dev-only fallback so a fresh checkout runs. Never used in production.
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-only-insecure-secret-key"
    MOCK_SMS_ECHO = _bool("MOCK_SMS_ECHO", True)
    ALLOW_MOCK_PAYMENT_PAGE = True


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-only-secret-key"
    SQLALCHEMY_DATABASE_URI = _database_url("TEST_DATABASE_URL")
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    SMS_PROVIDER = "mock"
    MOCK_SMS_ECHO = False
    PAYMENT_PROVIDER = "mock"
    MOCK_PAYMENT_SECRET = "test-mock-payment-secret"
    ALLOW_MOCK_PAYMENT_PAGE = True

    @classmethod
    def validate(cls, config: dict) -> None:
        uri = config.get("SQLALCHEMY_DATABASE_URI")
        if not uri:
            raise RuntimeError("TEST_DATABASE_URL is not set (see .env.example).")
        db_name = make_url(uri).database or ""
        if not db_name.endswith("_test"):
            # The test suite drops and recreates the schema. Never point it at real data.
            raise RuntimeError(
                f"Refusing to run tests against database '{db_name}': "
                "the test database name must end with '_test'."
            )


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"

    @classmethod
    def validate(cls, config: dict) -> None:
        super().validate(config)
        if len(config["SECRET_KEY"]) < 32:
            raise RuntimeError("SECRET_KEY must be at least 32 characters in production.")
        if (config.get("SMS_PROVIDER") or "").lower() == "mock":
            raise RuntimeError("SMS_PROVIDER=mock is not allowed in production.")
        provider = (config.get("PAYMENT_PROVIDER") or "").lower()
        if provider == "mock":
            raise RuntimeError("PAYMENT_PROVIDER=mock is not allowed in production.")
        if provider == "intasend" and not (config.get("INTASEND_SECRET_KEY") and config.get("INTASEND_WEBHOOK_CHALLENGE")):
            raise RuntimeError("INTASEND_SECRET_KEY and INTASEND_WEBHOOK_CHALLENGE are required in production.")


_CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str):
    try:
        return _CONFIGS[name]
    except KeyError:
        raise RuntimeError(
            f"Unknown APP_ENV '{name}'. Use one of: {', '.join(_CONFIGS)}."
        ) from None
