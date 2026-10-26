import secrets
from datetime import datetime, timedelta

from flask import Blueprint, current_app, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.extensions import db, limiter
from app.models.booking import BookingIntent
from app.models.payment import PaymentAttempt
from app.services import availability_service as av
from app.services import booking_service, business_service, catalog_service, payment_service
from app.services.errors import ValidationError
from app.utils.fees import payment_fee_kes, percent_label
from app.utils.phone import mask_phone
from app.utils.timeutil import utcnow

bp = Blueprint("bookings", __name__)

STRIP_DAYS = 7


def _public_business(slug):
    """Booking is only for live public businesses (an owner previewing a hidden page cannot book)."""
    business, preview = business_service.get_viewable_business(slug, current_user)
    if business is None or preview:
        abort(404)
    return business


def _service_or_404(business, raw_id):
    service = catalog_service.get_bookable_service(business, int(raw_id)) if str(raw_id).isdigit() else None
    if service is None:
        abort(404)
    return service


@bp.get("/<slug>/slots")
def slots(slug):
    business = _public_business(slug)
    service = _service_or_404(business, request.args.get("service_id", ""))
    tz = av._business_tz(business)
    today = utcnow().astimezone(tz).date()
    try:
        day = datetime.strptime(request.args.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        day = today
    day = max(today, day)
    options = av.get_slots_for_day(business, service, day)
    groups = [
        ("Morning", [s for s in options if s.hour < 12]),
        ("Afternoon", [s for s in options if 12 <= s.hour < 17]),
        ("Evening", [s for s in options if s.hour >= 17]),
    ]
    return render_template(
        "bookings/slots.html", business=business, service=service, day=day, today=today,
        strip=[today + timedelta(days=i) for i in range(STRIP_DAYS)],
        max_day=today + timedelta(days=current_app.config["BOOKING_MAX_ADVANCE_DAYS"]),
        groups=[(label, items) for label, items in groups if items], has_slots=bool(options),
        fee_label=percent_label(current_app.config["PAYMENT_FEE_BPS"]),
    )


def _details(business, service, start_raw, values=None, status=200):
    try:
        start = booking_service.parse_start(start_raw)
    except booking_service.SlotUnavailable:
        return redirect(url_for("bookings.slots", slug=business.slug, service_id=service.id))
    if not av.is_slot_available(business, service, start):
        flash("Sorry, that time is no longer available. Please pick another.", "error")
        day = start.astimezone(av._business_tz(business)).date().isoformat()
        return redirect(url_for("bookings.slots", slug=business.slug, service_id=service.id, date=day))
    return render_template(
        "bookings/details.html", business=business, service=service, start=start, start_raw=start_raw,
        key=booking_service.new_idempotency_key(), values=values or {},
        day=start.astimezone(av._business_tz(business)).date(),
        fee=payment_fee_kes(service.price_kes, current_app.config["PAYMENT_FEE_BPS"]),
        fee_label=percent_label(current_app.config["PAYMENT_FEE_BPS"]),
    ), status


@bp.get("/<slug>/book")
def book(slug):
    business = _public_business(slug)
    service = _service_or_404(business, request.args.get("service_id", ""))
    return _details(business, service, request.args.get("start", ""))


@bp.post("/booking-intents")
@limiter.limit("20 per hour", scope="booking_intent_ip")
def create_intent():
    business = _public_business(request.form.get("slug", "").strip().lower())
    service = _service_or_404(business, request.form.get("service_id", ""))
    start_raw = request.form.get("start", "")
    try:
        intent = booking_service.create_hold(
            business, service.id, start_raw, request.form.get("name"), request.form.get("phone"),
            request.form.get("idempotency_key"))
    except booking_service.SlotUnavailable as exc:
        flash(str(exc), "error")
        return redirect(url_for("bookings.slots", slug=business.slug, service_id=service.id))
    except ValidationError as exc:
        flash(str(exc), "error")
        return _details(business, service, start_raw, values=request.form, status=400)
    return redirect(url_for("bookings.status", token=intent.public_token))


def _intent_or_404(token):
    intent = booking_service.get_intent(token)
    if intent is None:
        abort(404)
    return intent


def _breakdown(intent):
    return {"price": intent.amount_kes_snapshot, "fee": intent.fee_kes_snapshot, "total": intent.total_kes,
            "fee_label": percent_label(intent.fee_bps_snapshot)}


@bp.get("/booking-status/<token>")
@limiter.limit("120 per hour", scope="booking_status_ip")
def status(token):
    intent = _intent_or_404(token)
    business = business_service.get_business_by_id(intent.business_id)
    view = payment_service.payment_view(intent)
    return render_template(
        "bookings/status.html", intent=intent, business=business, view=view, money=_breakdown(intent),
        seconds=booking_service.seconds_left(intent),
        mock_ref=_mock_ref(view) if _mock_enabled() else None, masked_phone=mask_phone(intent.customer_phone),
    )


@bp.get("/booking-status/<token>/json")
@limiter.limit("600 per hour", scope="booking_status_json_ip")
def status_json(token):
    intent = _intent_or_404(token)
    payment_service.maybe_reconcile(intent)     # asks the provider if a callback is overdue
    view = payment_service.payment_view(intent)
    return jsonify(status=booking_service.effective_status(intent), state=view.state,
                   seconds_left=booking_service.seconds_left(intent))


@bp.post("/booking-status/<token>/pay")
@limiter.limit("15 per hour", scope="booking_pay_ip")
def pay(token):
    intent = _intent_or_404(token)
    try:
        payment_service.start_payment(intent)
    except payment_service.PaymentError as exc:
        flash(str(exc), "error")
    return redirect(url_for("bookings.status", token=token))


@bp.post("/booking-status/<token>/release")
def release(token):
    intent = _intent_or_404(token)
    business = business_service.get_business_by_id(intent.business_id)
    if payment_service.payment_view(intent).state in ("waiting", "paid", "review"):
        flash("A payment is in progress for this booking, so it cannot be released now.", "error")
        return redirect(url_for("bookings.status", token=token))
    if booking_service.release_hold(intent):
        flash("Your slot has been released.", "success")
    return redirect(url_for("public.business_page", slug=business.slug))


# ------------------------------------------------- development-only "fake phone" for the mock provider
def _mock_enabled() -> bool:
    cfg = current_app.config
    return bool(cfg.get("ALLOW_MOCK_PAYMENT_PAGE")) and current_app.extensions["payment_provider"].name == "mock"


def _mock_ref(view):
    return view.attempt.provider_reference if view.attempt and view.state == "waiting" else None


MOCK_ACTIONS = {
    "paid": "Customer entered PIN and paid the right amount",
    "paid_twice": "Same success message delivered twice (duplicate callback)",
    "paid_wrong_amount": "Success message with the WRONG amount",
    "cancelled": "Customer dismissed the prompt",
    "failed_funds": "Failed: insufficient M-Pesa balance",
}


def _mock_attempt_or_404(ref):
    if not _mock_enabled():
        abort(404)
    attempt = db.session.scalar(select(PaymentAttempt).where(PaymentAttempt.provider_reference == ref))
    if attempt is None:
        abort(404)
    return attempt


@bp.post("/dev/mock-pay/<ref>/<action>")
def mock_pay(ref, action):
    attempt = _mock_attempt_or_404(ref)
    if action not in MOCK_ACTIONS:
        abort(404)
    intent = db.session.get(BookingIntent, attempt.booking_intent_id)
    prov = current_app.extensions["payment_provider"]
    base = dict(provider_reference=attempt.provider_reference, our_reference=attempt.our_reference)
    receipt = "MOCK" + secrets.token_hex(4).upper()
    if action in ("paid", "paid_twice"):
        message = dict(base, status="succeeded", amount_kes=attempt.amount_kes, receipt=receipt)
    elif action == "paid_wrong_amount":
        message = dict(base, status="succeeded", amount_kes=attempt.amount_kes - 1, receipt=receipt)
    elif action == "cancelled":
        message = dict(base, status="cancelled", result_code="1032", description="Request cancelled by user")
    else:
        message = dict(base, status="failed", result_code="1", description="Insufficient balance")
    outcomes = []
    for _ in range(2 if action == "paid_twice" else 1):
        body, headers = prov.build_body(**message)
        outcomes.append(payment_service.handle_webhook("mock", body, headers))
    flash(f"Mock phone: {MOCK_ACTIONS[action]} -> {', '.join(outcomes)}", "info")
    return redirect(url_for("bookings.status", token=intent.public_token))
