from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.models.user import ROLE_OWNER
from app.security import role_required
from app.services import availability_service as av
from app.services import business_service, catalog_service
from app.services.errors import ValidationError
from app.utils.timeutil import NAIROBI, utcnow

bp = Blueprint("availability", __name__)


def _business():
    return business_service.get_business_for_owner(current_user.id)


def _need_business():
    flash("Set up your business profile first.", "info")
    return redirect(url_for("owner.profile"))


def _preview(business, services):
    """Owner-only 'what would customers see' check. Returns (selected_service, day, slots) or Nones."""
    raw_id, raw_day = request.args.get("service_id", ""), request.args.get("date", "")
    if not raw_id and not raw_day:
        return None, None, None
    service = catalog_service.get_owned_service(business, int(raw_id)) if raw_id.isdigit() else None
    try:
        day = datetime.strptime(raw_day, "%Y-%m-%d").date()
    except ValueError:
        day = None
    if service is None or day is None:
        flash("Choose a service and a valid date to preview slots.", "error")
        return None, None, None
    return service, day, av.get_slots_for_day(business, service, day)


@bp.get("/availability")
@role_required(ROLE_OWNER)
def index():
    business = _business()
    if business is None:
        return _need_business()
    windows, breaks = av.list_weekly(business.id)
    week = [(d, av.WEEKDAYS[d], windows[d], breaks[d]) for d in range(7)]
    services = catalog_service.list_services(business.id)
    service, day, slots = _preview(business, services)
    return render_template(
        "availability/index.html", business=business, week=week, services=services,
        preview_service=service, preview_day=day, preview_slots=slots,
        tomorrow=(utcnow().astimezone(NAIROBI) + timedelta(days=1)).date().isoformat(),
    )


def _add(kind: str, done_message: str):
    business = _business()
    if business is None:
        return _need_business()
    try:
        av.add_weekly_entries(business, request.form, kind)
    except ValidationError as exc:
        flash(str(exc), "error")
    else:
        flash(done_message, "success")
    return redirect(url_for("availability.index"))


def _delete(kind: str, entry_id: int, done_message: str):
    business = _business()
    if business is None:
        return _need_business()
    if not av.delete_weekly_entry(business, entry_id, kind):
        abort(404)
    flash(done_message, "success")
    return redirect(url_for("availability.index"))


@bp.post("/availability/hours")
@role_required(ROLE_OWNER)
def add_hours():
    return _add("hours", "Opening hours saved.")


@bp.post("/availability/hours/<int:entry_id>/delete")
@role_required(ROLE_OWNER)
def delete_hours(entry_id):
    return _delete("hours", entry_id, "Opening hours removed.")


@bp.post("/availability/breaks")
@role_required(ROLE_OWNER)
def add_break():
    return _add("break", "Break saved.")


@bp.post("/availability/breaks/<int:entry_id>/delete")
@role_required(ROLE_OWNER)
def delete_break(entry_id):
    return _delete("break", entry_id, "Break removed.")


@bp.route("/blocked-periods", methods=["GET", "POST"])
@role_required(ROLE_OWNER)
def blocked_periods():
    business = _business()
    if business is None:
        return _need_business()
    if request.method == "POST":
        try:
            av.add_blocked_period(business, request.form)
        except ValidationError as exc:
            flash(str(exc), "error")
            return render_template(
                "availability/blocked.html", business=business, values=request.form,
                periods=av.list_upcoming_blocked(business.id),
            ), 400
        flash("Blocked period saved. Customers cannot book during it.", "success")
        return redirect(url_for("availability.blocked_periods"))
    return render_template(
        "availability/blocked.html", business=business, values={},
        periods=av.list_upcoming_blocked(business.id),
    )


@bp.post("/blocked-periods/<int:period_id>/delete")
@role_required(ROLE_OWNER)
def delete_blocked_period(period_id):
    business = _business()
    if business is None:
        return _need_business()
    if not av.delete_blocked_period(business, period_id):
        abort(404)
    flash("Blocked period removed.", "success")
    return redirect(url_for("availability.blocked_periods"))
