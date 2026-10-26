import logging

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_limiter.util import get_remote_address
from flask_login import current_user, login_user, logout_user

from app.extensions import limiter
from app.security import home_endpoint_for, is_safe_next_url
from app.services import auth_service
from app.services.auth_service import PhoneAlreadyRegistered, ValidationError
from app.utils.phone import mask_phone, normalize_ke_phone

bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)

PENDING_VERIFY = "pending_verify_phone"
PENDING_RESET = "pending_reset_phone"


def _form_phone_key() -> str:
    return normalize_ke_phone(request.form.get("phone")) or get_remote_address()


def _verify_phone_key() -> str:
    return session.get(PENDING_VERIFY) or get_remote_address()


def _reset_phone_key() -> str:
    return session.get(PENDING_RESET) or get_remote_address()


def _already_logged_in():
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint_for(current_user)))
    return None


# ------------------------------------------------------------------ register
@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"], scope="register_ip")
def register():
    if (resp := _already_logged_in()) is not None:
        return resp
    phone = ""
    if request.method == "POST":
        phone = request.form.get("phone", "")
        password = request.form.get("password", "")
        try:
            if password != request.form.get("confirm_password", ""):
                raise ValidationError("Passwords do not match.")
            user = auth_service.register_owner(phone, password)
        except PhoneAlreadyRegistered:
            flash("This phone number is already registered. Please log in instead.", "error")
            return render_template("auth/register.html", phone=phone), 409
        except ValidationError as exc:
            flash(str(exc), "error")
            return render_template("auth/register.html", phone=phone), 400
        session.clear()
        session[PENDING_VERIFY] = user.phone_e164
        flash("We sent a 6-digit code to your phone. Enter it to finish signing up.", "success")
        return redirect(url_for("auth.verify_phone"))
    return render_template("auth/register.html", phone=phone)


@bp.route("/verify-phone", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], scope="verify_ip")
@limiter.limit("10 per hour", key_func=_verify_phone_key, methods=["POST"], scope="verify_phone")
def verify_phone():
    phone = session.get(PENDING_VERIFY)
    if not phone:
        return redirect(url_for("auth.register"))
    if request.method == "POST":
        user = auth_service.verify_signup(phone, request.form.get("code", ""))
        if user is None:
            flash("That code is invalid or has expired.", "error")
            return render_template("auth/verify_phone.html", masked_phone=mask_phone(phone)), 400
        session.clear()
        login_user(user)
        flash("Phone verified. Welcome to AFRIBOOK!", "success")
        return redirect(url_for(home_endpoint_for(user)))
    return render_template("auth/verify_phone.html", masked_phone=mask_phone(phone))


@bp.post("/verify-phone/resend")
@limiter.limit("5 per hour", scope="resend_ip")
def resend_verification():
    phone = session.get(PENDING_VERIFY)
    if not phone:
        return redirect(url_for("auth.register"))
    if auth_service.resend_signup_otp(phone):
        flash("A new code has been sent.", "success")
    else:
        flash("Too many codes requested. Please wait before trying again.", "error")
    return redirect(url_for("auth.verify_phone"))


# --------------------------------------------------------------------- login
@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], scope="login_ip")
@limiter.limit("5 per minute", key_func=_form_phone_key, methods=["POST"], scope="login_phone")
def login():
    if (resp := _already_logged_in()) is not None:
        return resp
    next_url = request.values.get("next", "")
    if request.method == "POST":
        result = auth_service.authenticate(request.form.get("phone", ""), request.form.get("password", ""))
        if result.status == "ok":
            session.clear()
            login_user(result.user)
            if is_safe_next_url(next_url):
                return redirect(next_url)
            return redirect(url_for(home_endpoint_for(result.user)))
        if result.status == "unverified":
            session.clear()
            session[PENDING_VERIFY] = result.user.phone_e164
            auth_service.resend_signup_otp(result.user.phone_e164)
            flash("Please verify your phone number to continue.", "info")
            return redirect(url_for("auth.verify_phone"))
        logger.info("login_failed phone=%s", mask_phone(normalize_ke_phone(request.form.get("phone"))))
        flash("Invalid phone number or password.", "error")
        return render_template("auth/login.html", phone=request.form.get("phone", ""), next_url=next_url), 401
    return render_template("auth/login.html", phone="", next_url=next_url)


@bp.post("/logout")
def logout():
    logout_user()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


# ------------------------------------------------------------ password reset
@bp.route("/password-reset", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"], scope="reset_ip")
@limiter.limit("3 per hour", key_func=_form_phone_key, methods=["POST"], scope="reset_phone")
def password_reset_request():
    if request.method == "POST":
        phone = normalize_ke_phone(request.form.get("phone"))
        if phone is None:
            flash("Enter a valid Kenyan mobile number, e.g. 0712 345 678.", "error")
            return render_template("auth/password_reset_request.html"), 400
        auth_service.request_password_reset(phone)
        session.clear()
        session[PENDING_RESET] = phone
        # Same response whether or not the account exists.
        flash("If that number has an AFRIBOOK account, we sent a reset code to it.", "info")
        return redirect(url_for("auth.password_reset_confirm"))
    return render_template("auth/password_reset_request.html")


@bp.route("/password-reset/confirm", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], scope="reset_confirm_ip")
@limiter.limit("10 per hour", key_func=_reset_phone_key, methods=["POST"], scope="reset_confirm_phone")
def password_reset_confirm():
    phone = session.get(PENDING_RESET)
    if not phone:
        return redirect(url_for("auth.password_reset_request"))
    if request.method == "POST":
        try:
            new_password = request.form.get("new_password", "")
            if new_password != request.form.get("confirm_password", ""):
                raise ValidationError("Passwords do not match.")
            changed = auth_service.reset_password(phone, request.form.get("code", ""), new_password)
        except ValidationError as exc:
            flash(str(exc), "error")
            return render_template("auth/password_reset_confirm.html"), 400
        if not changed:
            flash("That code is invalid or has expired.", "error")
            return render_template("auth/password_reset_confirm.html"), 400
        session.clear()
        flash("Password updated. Please log in with your new password.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/password_reset_confirm.html")
