"""Charging customers and turning a verified payment into a confirmed booking.

Rules (Doc 2 section 7):
  * A booking exists ONLY after a verified successful payment (this module is the only creator).
  * The browser never decides that payment worked; only the provider's callback/status check does.
  * Callbacks are idempotent: repeating one changes nothing.
  * Money that cannot be honoured automatically (wrong amount, slot gone) goes to `review`, never to
    an overlapping booking.
Everything that touches a business's calendar takes the same advisory lock as booking_service.create_hold.
"""
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from flask import current_app
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.booking import BOOKING_CONFIRMED, STATUS_EXPIRED, STATUS_PAID, STATUS_PENDING, STATUS_REVIEW, Booking, BookingIntent
from app.models.payment import (ATTEMPT_ACTIVE, ATTEMPT_CANCELLED, ATTEMPT_FAILED, ATTEMPT_INITIATED, ATTEMPT_PENDING,
                                ATTEMPT_REVIEW, ATTEMPT_SUCCEEDED, ATTEMPT_TIMEOUT, PaymentAttempt)
from app.payments.base import (EVENT_CANCELLED, EVENT_FAILED, EVENT_PENDING, EVENT_SUCCEEDED, InvalidWebhook,
                               PaymentEvent, ProviderUnavailable)
from app.services import availability_service, booking_service
from app.models.business import Business
from app.utils.slots import overlaps
from app.utils.timeutil import utcnow

log = logging.getLogger(__name__)

MAX_ATTEMPTS_PER_INTENT = 3
STALE_ATTEMPT_MINUTES = 15
RECHECK_SECONDS = 30
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I


class PaymentError(Exception):
    """Something the customer can be told about (safe message)."""


def provider():
    return current_app.extensions["payment_provider"]


def _lock_day(business_id: int, local_day) -> None:
    db.session.execute(text("SELECT pg_advisory_xact_lock(CAST(:a AS integer), CAST(:b AS integer))"),
                       {"a": business_id, "b": local_day.toordinal()})


def _local_day(intent: BookingIntent):
    business = db.session.get(Business, intent.business_id)
    return business, intent.starts_at.astimezone(availability_service._business_tz(business)).date()


def latest_attempt(intent_id: int) -> PaymentAttempt | None:
    return db.session.scalar(
        select(PaymentAttempt).where(PaymentAttempt.booking_intent_id == intent_id)
        .order_by(PaymentAttempt.id.desc()).limit(1))


def booking_for_intent(intent_id: int) -> Booking | None:
    return db.session.scalar(select(Booking).where(Booking.booking_intent_id == intent_id))


# --------------------------------------------------------------------------- start
def start_payment(intent: BookingIntent, now: datetime | None = None) -> PaymentAttempt:
    """Create an attempt and ask the provider to send the M-Pesa prompt. Safe to call twice (double-click)."""
    cfg = current_app.config
    now = now or utcnow()
    business, day = _local_day(intent)
    try:
        _lock_day(business.id, day)
        db.session.refresh(intent)
        if booking_service.effective_status(intent, now) != STATUS_PENDING:
            db.session.rollback()
            raise PaymentError("This hold has ended. Please choose a time again.")
        active = db.session.scalar(select(PaymentAttempt).where(
            PaymentAttempt.booking_intent_id == intent.id, PaymentAttempt.status.in_(ATTEMPT_ACTIVE)))
        if active is not None:
            db.session.rollback()
            return active  # the prompt is already on the customer's phone
        tried = db.session.scalar(select(func.count()).select_from(PaymentAttempt)
                                  .where(PaymentAttempt.booking_intent_id == intent.id))
        if tried >= MAX_ATTEMPTS_PER_INTENT:
            db.session.rollback()
            raise PaymentError("Too many payment attempts. Please start again.")

        attempt = PaymentAttempt(
            booking_intent_id=intent.id, provider=provider().name, our_reference="AB" + secrets.token_hex(10).upper(),
            amount_kes=intent.total_kes, customer_phone=intent.customer_phone, status=ATTEMPT_INITIATED)
        db.session.add(attempt)
        # Stretch the hold so the customer has time to type their PIN. Never shortens it.
        intent.hold_expires_at = max(intent.hold_expires_at, now + timedelta(minutes=cfg["PAYMENT_GRACE_MINUTES"]))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise PaymentError("A payment is already in progress for this booking.") from None

    # The provider call happens outside the database transaction (it is a slow network call).
    result = provider().initiate_stk(
        our_reference=attempt.our_reference, phone_e164=attempt.customer_phone, amount_kes=attempt.amount_kes,
        narrative=f"{intent.service_name_snapshot} booking")
    if not result.ok:
        attempt.status = ATTEMPT_FAILED
        attempt.result_description = (result.message or "Could not send the prompt")[:200]
        attempt.completed_at = utcnow()
        db.session.commit()
        raise PaymentError(result.message or "The M-Pesa prompt could not be sent. Please try again.")
    attempt.status = ATTEMPT_PENDING
    attempt.provider_reference = result.provider_reference
    db.session.commit()
    return attempt


# ------------------------------------------------------------------------ callbacks
def handle_webhook(provider_name: str, body: bytes, headers, now: datetime | None = None) -> str:
    """Entry point for provider callbacks. Raises InvalidWebhook (reject) or ProviderUnavailable (retry later)."""
    prov = provider()
    if provider_name != prov.name:
        raise InvalidWebhook("provider not active")
    event = prov.parse_webhook(body, headers)
    return process_event(event, now=now)


def _find_attempt(event: PaymentEvent) -> PaymentAttempt | None:
    if event.provider_reference:
        found = db.session.scalar(select(PaymentAttempt).where(PaymentAttempt.provider_reference == event.provider_reference))
        if found:
            return found
    if event.our_reference:
        return db.session.scalar(select(PaymentAttempt).where(PaymentAttempt.our_reference == event.our_reference))
    return None


def process_event(event: PaymentEvent, now: datetime | None = None, verified: bool = False) -> str:
    """Apply one provider event. Returns an outcome word used by tests and logs."""
    now = now or utcnow()
    attempt = _find_attempt(event)
    if attempt is None:
        log.warning("payment event for unknown reference (provider_ref=%s)", event.provider_reference)
        return "ignored"
    if event.status == EVENT_SUCCEEDED and not verified:
        event = provider().verify_event(event)   # may raise ProviderUnavailable: provider will retry / reconcile

    intent = db.session.get(BookingIntent, attempt.booking_intent_id)
    business, day = _local_day(intent)
    try:
        _lock_day(business.id, day)
        db.session.refresh(attempt)
        db.session.refresh(intent)
        outcome = _apply(attempt, intent, business, event, now)
        db.session.commit()
        return outcome
    except IntegrityError:
        # A concurrent duplicate or a database safety net (exclusion/unique constraint) fired.
        db.session.rollback()
        attempt = db.session.get(PaymentAttempt, attempt.id)
        if attempt.status in (ATTEMPT_SUCCEEDED, ATTEMPT_REVIEW):
            return "duplicate"
        intent = db.session.get(BookingIntent, attempt.booking_intent_id)
        _send_to_review(attempt, intent, "Database refused the booking (possible overlap or duplicate).", now)
        db.session.commit()
        return "review"


def _clean_payload(raw: dict) -> dict:
    return {k: v for k, v in (raw or {}).items() if k.lower() not in {"challenge", "secret", "pin"}}


def _send_to_review(attempt: PaymentAttempt, intent: BookingIntent, reason: str, now: datetime) -> None:
    attempt.status = ATTEMPT_REVIEW
    attempt.result_description = reason[:200]
    attempt.completed_at = now
    intent.status = STATUS_REVIEW
    log.warning("PAYMENT NEEDS REVIEW attempt=%s intent=%s reason=%s", attempt.id, intent.id, reason)


def _new_reference() -> str:
    while True:
        ref = "AB-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
        if db.session.scalar(select(Booking.id).where(Booking.public_reference == ref)) is None:
            return ref


def _apply(attempt, intent, business, event: PaymentEvent, now: datetime) -> str:
    if attempt.status in (ATTEMPT_SUCCEEDED, ATTEMPT_REVIEW):
        return "duplicate"   # money already dealt with; a repeated message changes nothing

    attempt.callback_received_at = now
    attempt.callback_payload = _clean_payload(event.raw)
    attempt.result_code = event.result_code
    attempt.result_description = (event.description or None) and event.description[:200]

    if event.status == EVENT_PENDING:
        return "pending"
    if event.status in (EVENT_FAILED, EVENT_CANCELLED):
        attempt.status = ATTEMPT_CANCELLED if event.status == EVENT_CANCELLED else ATTEMPT_FAILED
        attempt.completed_at = now
        return event.status   # the hold stays until it expires so the customer can try again

    # ---- success: every check must pass before a booking exists
    if event.amount_kes is None or event.amount_kes != attempt.amount_kes:
        _send_to_review(attempt, intent, f"Amount mismatch: expected {attempt.amount_kes}, provider says {event.amount_kes}.", now)
        attempt.mpesa_receipt = event.receipt
        return "review"
    if intent.status in (STATUS_PAID, STATUS_REVIEW):
        _send_to_review(attempt, intent, "Payment received for a booking that was already settled.", now)
        attempt.mpesa_receipt = event.receipt
        return "review"

    tz = availability_service._business_tz(business)
    day_start = datetime.combine(intent.starts_at.astimezone(tz).date(), time.min, tzinfo=tz)
    taken = availability_service.busy_intervals(
        business.id, day_start, day_start + timedelta(days=1), now, exclude_intent_id=intent.id)
    if any(overlaps(intent.starts_at, intent.ends_at, start, end) for start, end in taken):
        attempt.mpesa_receipt = event.receipt
        _send_to_review(attempt, intent, "Paid after the slot was released and taken by someone else.", now)
        return "review"

    attempt.status = ATTEMPT_SUCCEEDED
    attempt.mpesa_receipt = event.receipt
    attempt.completed_at = now
    intent.status = STATUS_PAID
    db.session.flush()
    db.session.add(Booking(
        public_reference=_new_reference(), booking_intent_id=intent.id, payment_attempt_id=attempt.id,
        business_id=intent.business_id, service_id=intent.service_id,
        customer_name_snapshot=intent.customer_name, customer_phone_snapshot=intent.customer_phone,
        service_name_snapshot=intent.service_name_snapshot, duration_minutes_snapshot=intent.duration_minutes_snapshot,
        amount_kes_snapshot=intent.amount_kes_snapshot, fee_kes_snapshot=intent.fee_kes_snapshot,
        starts_at=intent.starts_at, ends_at=intent.ends_at, status=BOOKING_CONFIRMED))
    return "confirmed"


# ---------------------------------------------------------------- missing callbacks
def reconcile_attempt(attempt: PaymentAttempt, now: datetime | None = None) -> str | None:
    """Ask the provider what happened to one pending attempt (callback never arrived)."""
    now = now or utcnow()
    try:
        event = provider().query_status(attempt.provider_reference, attempt.our_reference)
    except ProviderUnavailable:
        log.warning("reconcile: provider unavailable for attempt %s", attempt.id)
        return None
    attempt.last_checked_at = now
    db.session.commit()
    if event is None:
        return None
    if not event.our_reference:
        event.our_reference = attempt.our_reference
    event.provider_reference = event.provider_reference or attempt.provider_reference
    return process_event(event, now=now, verified=True)


def reconcile_pending(now: datetime | None = None) -> dict:
    """Worker/CLI job: check overdue pending attempts, then time out the ones nobody answered."""
    cfg = current_app.config
    now = now or utcnow()
    due = list(db.session.scalars(select(PaymentAttempt).where(
        PaymentAttempt.status == ATTEMPT_PENDING,
        PaymentAttempt.initiated_at <= now - timedelta(seconds=cfg["PAYMENT_RECONCILE_AFTER_SECONDS"]))))
    results = {"checked": 0, "resolved": 0, "timed_out": 0}
    for attempt in due:
        results["checked"] += 1
        outcome = reconcile_attempt(attempt, now)
        if outcome in ("confirmed", "review", "failed", "cancelled"):
            results["resolved"] += 1
        db.session.refresh(attempt)
        if attempt.status == ATTEMPT_PENDING and attempt.initiated_at <= now - timedelta(minutes=STALE_ATTEMPT_MINUTES):
            attempt.status = ATTEMPT_TIMEOUT
            attempt.completed_at = now
            db.session.commit()
            results["timed_out"] += 1
    return results


def maybe_reconcile(intent: BookingIntent, now: datetime | None = None) -> None:
    """Called while the customer's page polls: nudge the provider if the callback is overdue."""
    now = now or utcnow()
    attempt = latest_attempt(intent.id)
    if attempt is None or attempt.status != ATTEMPT_PENDING:
        return
    cfg = current_app.config
    if attempt.initiated_at > now - timedelta(seconds=cfg["PAYMENT_RECONCILE_AFTER_SECONDS"]):
        return
    if attempt.last_checked_at and attempt.last_checked_at > now - timedelta(seconds=RECHECK_SECONDS):
        return
    reconcile_attempt(attempt, now)


# ------------------------------------------------------------------- customer view
@dataclass
class PaymentView:
    state: str            # payable | waiting | paid | retry | expired | review
    message: str = ""
    attempt: PaymentAttempt | None = None
    booking: Booking | None = None


def payment_view(intent: BookingIntent, now: datetime | None = None) -> PaymentView:
    now = now or utcnow()
    attempt = latest_attempt(intent.id)
    booking = booking_for_intent(intent.id)
    if intent.status == STATUS_PAID and booking is not None:
        return PaymentView("paid", attempt=attempt, booking=booking)
    if intent.status == STATUS_REVIEW or (attempt and attempt.status == ATTEMPT_REVIEW):
        return PaymentView("review", attempt=attempt)
    if booking_service.effective_status(intent, now) != STATUS_PENDING:
        return PaymentView("expired", attempt=attempt)
    if attempt and attempt.status in ATTEMPT_ACTIVE:
        return PaymentView("waiting", attempt=attempt)
    if attempt and attempt.status in (ATTEMPT_FAILED, ATTEMPT_CANCELLED, ATTEMPT_TIMEOUT):
        msg = {"cancelled": "You cancelled the M-Pesa prompt.", "timeout": "We did not get your payment in time."}.get(
            attempt.status, "The payment did not go through.")
        return PaymentView("retry", message=msg, attempt=attempt)
    return PaymentView("payable", attempt=attempt)
