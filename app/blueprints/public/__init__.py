from flask import Blueprint, abort, redirect, render_template, url_for
from flask_login import current_user

from app.security import home_endpoint_for
from app.services import availability_service, business_service, catalog_service

bp = Blueprint("public", __name__)


@bp.get("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for(home_endpoint_for(current_user)))
    return render_template("public/index.html")


@bp.get("/<slug>")
def business_page(slug):
    """Public booking page: afribook.co.ke/<slug>. Static routes (e.g. /login) always win over this."""
    business, preview = business_service.get_viewable_business(slug, current_user)
    if business is None:
        abort(404)
    services = catalog_service.list_active_services(business.id)  # inactive services are never shown
    return render_template("public/business.html", business=business, preview=preview, services=services,
                           hours=availability_service.weekly_summary(business.id))
