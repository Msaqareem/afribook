"""Authorization helpers shared by blueprints."""
from functools import wraps
from urllib.parse import urlparse

from flask import abort, current_app
from flask_login import current_user

import hmac

from app.extensions import db, login_manager
from app.models.user import ROLE_ADMIN, User


@login_manager.user_loader
def load_user(session_id: str):
    """Session id is '<user id>.<password fingerprint>'; a password change invalidates it."""
    try:
        user_id, fingerprint = session_id.split(".", 1)
        user = db.session.get(User, int(user_id))
    except (ValueError, AttributeError):
        return None
    if user is None or not user.is_active:
        return None
    if not hmac.compare_digest(user._fingerprint(), fingerprint):
        return None
    return user


def role_required(*roles: str):
    """Require a logged-in user with one of the given roles (403 otherwise)."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def home_endpoint_for(user) -> str:
    return "admin.home" if user.role == ROLE_ADMIN else "owner.dashboard"


def is_safe_next_url(target: str | None) -> bool:
    """Only allow same-site relative redirects (blocks open redirects)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc
