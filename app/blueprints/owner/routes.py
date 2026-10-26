from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.models.business import PAYMENT_PAYBILL, PAYMENT_TILL
from app.models.user import ROLE_OWNER
from app.security import role_required
from app.services import availability_service, business_service, catalog_service
from app.services.errors import ValidationError
from app.utils.kenya import KENYA_COUNTIES

bp = Blueprint("owner", __name__)


def _profile_defaults(business) -> dict:
    if business is None:
        return {"contact_phone": current_user.phone_e164, "is_public": "on"}
    return {
        "name": business.name, "slug": business.slug, "category_id": str(business.category_id),
        "county": business.county, "address": business.address,
        "contact_phone": business.contact_phone, "description": business.description or "",
        "is_public": "on" if business.is_public else "",
    }


@bp.get("/dashboard")
@role_required(ROLE_OWNER)
def dashboard():
    business = business_service.get_business_for_owner(current_user.id)
    total, active = catalog_service.count_services(business.id) if business else (0, 0)
    open_days = (sum(1 for _, w in availability_service.weekly_summary(business.id) if w)
                 if business else 0)
    return render_template("owner/dashboard.html", business=business, service_total=total,
                           service_active=active, open_days=open_days)


@bp.route("/settings/profile", methods=["GET", "POST"])
@role_required(ROLE_OWNER)
def profile():
    business = business_service.get_business_for_owner(current_user.id)
    categories = business_service.list_active_categories()

    def render(values, status=200):
        return render_template(
            "owner/profile.html", business=business, values=values,
            category_options=[(c.id, c.name) for c in categories],
            county_options=[(name, name) for name in KENYA_COUNTIES],
        ), status

    if request.method == "POST":
        creating = business is None
        try:
            business = business_service.save_profile(current_user, request.form)
        except ValidationError as exc:
            flash(str(exc), "error")
            return render(request.form, 400)
        flash(
            "Business created. Next, add your payment details." if creating else "Business profile saved.",
            "success",
        )
        return redirect(url_for("owner.payment" if creating else "owner.dashboard"))
    return render(_profile_defaults(business))


@bp.route("/settings/payment", methods=["GET", "POST"])
@role_required(ROLE_OWNER)
def payment():
    business = business_service.get_business_for_owner(current_user.id)
    if business is None:
        flash("Set up your business profile first.", "info")
        return redirect(url_for("owner.profile"))

    account = business.payment_account
    defaults = {
        "payment_type": account.payment_type if account else PAYMENT_TILL,
        "shortcode": account.shortcode if account else "",
        "account_reference_template": (account.account_reference_template or "") if account else "",
    }

    def render(values, status=200):
        return render_template(
            "owner/payment.html", business=business, account=account, values=values,
            till=PAYMENT_TILL, paybill=PAYMENT_PAYBILL,
        ), status

    if request.method == "POST":
        try:
            business_service.save_payment_account(business, request.form)
        except ValidationError as exc:
            flash(str(exc), "error")
            return render(request.form, 400)
        flash("Payment details saved.", "success")
        return redirect(url_for("owner.dashboard"))
    return render(defaults)
