from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.models.user import ROLE_OWNER
from app.security import role_required
from app.services import business_service, catalog_service
from app.services.errors import ValidationError

bp = Blueprint("catalog", __name__, url_prefix="/services")


def _business():
    return business_service.get_business_for_owner(current_user.id)


def _need_business():
    flash("Set up your business profile first.", "info")
    return redirect(url_for("owner.profile"))


@bp.get("/")
@role_required(ROLE_OWNER)
def list_services():
    business = _business()
    if business is None:
        return _need_business()
    return render_template(
        "catalog/list.html", business=business, services=catalog_service.list_services(business.id)
    )


@bp.route("/new", methods=["GET", "POST"])
@role_required(ROLE_OWNER)
def new_service():
    business = _business()
    if business is None:
        return _need_business()
    if request.method == "POST":
        try:
            catalog_service.save_service(business, request.form)
        except ValidationError as exc:
            flash(str(exc), "error")
            return render_template("catalog/form.html", service=None, values=request.form), 400
        flash("Service added.", "success")
        return redirect(url_for("catalog.list_services"))
    return render_template("catalog/form.html", service=None, values={"is_active": "on"})


@bp.route("/<int:service_id>/edit", methods=["GET", "POST"])
@role_required(ROLE_OWNER)
def edit_service(service_id):
    business = _business()
    if business is None:
        return _need_business()
    service = catalog_service.get_owned_service(business, service_id)
    if service is None:
        abort(404)  # 404, not 403: do not reveal that another business's service exists
    if request.method == "POST":
        try:
            catalog_service.save_service(business, request.form, service)
        except ValidationError as exc:
            flash(str(exc), "error")
            return render_template("catalog/form.html", service=service, values=request.form), 400
        flash("Service updated.", "success")
        return redirect(url_for("catalog.list_services"))
    values = {
        "name": service.name, "description": service.description or "",
        "price_kes": str(service.price_kes), "duration_minutes": str(service.duration_minutes),
        "is_active": "on" if service.is_active else "",
    }
    return render_template("catalog/form.html", service=service, values=values)


@bp.post("/<int:service_id>/toggle")
@role_required(ROLE_OWNER)
def toggle_service(service_id):
    business = _business()
    if business is None:
        return _need_business()
    service = catalog_service.get_owned_service(business, service_id)
    if service is None:
        abort(404)
    catalog_service.set_active(service, not service.is_active)
    flash(f"{service.name} is now {'active' if service.is_active else 'inactive'}.", "success")
    return redirect(url_for("catalog.list_services"))
