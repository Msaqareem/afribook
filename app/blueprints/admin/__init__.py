from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.models.user import ROLE_ADMIN
from app.security import role_required
from app.services import payout_service
from app.services.errors import ValidationError

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/")
@role_required(ROLE_ADMIN)
def home():
    owed = payout_service.owed_summary()
    return render_template("admin/home.html", owed_total=sum(r["total"] for r in owed), owed_count=len(owed),
                           review_count=len(payout_service.review_queue()))


# ------------------------------------------------------------------- payouts
@bp.get("/payouts")
@role_required(ROLE_ADMIN)
def payouts():
    return render_template("admin/payouts.html", owed=payout_service.owed_summary(),
                           payouts=payout_service.list_payouts())


@bp.post("/payouts/create")
@role_required(ROLE_ADMIN)
def create_payout():
    try:
        payout = payout_service.create_payout(int(request.form.get("business_id", "0") or 0), current_user.id)
    except (ValidationError, ValueError) as exc:
        flash(str(exc) or "Choose a business.", "error")
        return redirect(url_for("admin.payouts"))
    flash("Payout prepared. Send the money, then record the reference.", "success")
    return redirect(url_for("admin.payout_detail", payout_id=payout.id))


def _payout_or_404(payout_id):
    payout = payout_service.get_payout(payout_id)
    if payout is None:
        abort(404)
    return payout


@bp.get("/payouts/<int:payout_id>")
@role_required(ROLE_ADMIN)
def payout_detail(payout_id):
    payout = _payout_or_404(payout_id)
    from app.services.business_service import get_business_by_id
    return render_template("admin/payout_detail.html", payout=payout, business=get_business_by_id(payout.business_id),
                           bookings=payout_service.payout_bookings(payout.id))


@bp.post("/payouts/<int:payout_id>/paid")
@role_required(ROLE_ADMIN)
def payout_paid(payout_id):
    _payout_or_404(payout_id)
    try:
        payout_service.mark_paid(payout_id, request.form.get("reference"))
        flash("Payout recorded as paid.", "success")
    except ValidationError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.payout_detail", payout_id=payout_id))


@bp.post("/payouts/<int:payout_id>/cancel")
@role_required(ROLE_ADMIN)
def payout_cancel(payout_id):
    _payout_or_404(payout_id)
    try:
        payout_service.cancel_payout(payout_id)
        flash("Payout cancelled. Its bookings are payable again.", "success")
    except ValidationError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.payout_detail", payout_id=payout_id))


@bp.get("/payouts/<int:payout_id>/export.csv")
@role_required(ROLE_ADMIN)
def payout_export(payout_id):
    payout = _payout_or_404(payout_id)
    return Response(payout_service.payout_csv(payout), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=payout-{payout.id}.csv"})


# ------------------------------------------------------------ payments needing review
@bp.get("/payments/review")
@role_required(ROLE_ADMIN)
def payment_review():
    return render_template("admin/review.html", items=payout_service.review_queue())


@bp.post("/payments/<int:attempt_id>/resolve")
@role_required(ROLE_ADMIN)
def payment_resolve(attempt_id):
    try:
        payout_service.resolve_review(attempt_id, request.form.get("note"))
        flash("Marked as resolved.", "success")
    except ValidationError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.payment_review"))
