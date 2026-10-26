"""Booking holds: reserve a slot for a few minutes while the customer pays (Bucket 6 adds payment).

Double-booking protection (Doc 2 section 6.2): every hold is created in one PostgreSQL transaction that
first takes a transaction-scoped advisory lock for (business, local date), then re-checks availability
against paid bookings and unexpired holds. Concurrent requests for the same business/day queue up on
the lock, so two overlapping holds can never both succeed. The lock is released on commit/rollback.
"""
import re
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.booking import STATUS_EXPIRED, STATUS_PENDING, BookingIntent
from app.models.business import Business
from app.services import availability_service, catalog_service
from app.services.errors import ValidationError
from app.utils.phone import normalize_ke_phone
from app.utils.fees import payment_fee_kes
from app.utils.timeutil import utcnow

KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class SlotUnavailable(Exception):
    """The chosen time is no longer bookable (taken, blocked, closed or out of range)."""


def _clean_name(raw) -> str:
    name = " ".join((raw or "").split())
    if not (2 <= len(name) <= 80):
        raise ValidationError("Enter your name (2-80 characters).")
    return name


def _parse_start(raw) -> datetime:
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        raise SlotUnavailable("That time is not valid.") from None


def parse_start(raw) -> datetime:
    return _parse_start(raw)


def new_idempotency_key() -> str:
    return secrets.token_hex(16)


def get_intent(token: str) -> BookingIntent | None:
    if not token or len(token) > 64:
        return None
    return db.session.scalar(select(BookingIntent).where(BookingIntent.public_token == token))


def effective_status(intent: BookingIntent, now: datetime | None = None) -> str:
    """A pending hold past its expiry is treated as expired even before the worker marks it."""
    now = now or utcnow()
    if intent.status == STATUS_PENDING and intent.hold_expires_at <= now:
        return STATUS_EXPIRED
    return intent.status


def seconds_left(intent: BookingIntent, now: datetime | None = None) -> int:
    if effective_status(intent, now) != STATUS_PENDING:
        return 0
    return max(0, int((intent.hold_expires_at - (now or utcnow())).total_seconds()))


def create_hold(business: Business, service_id, start_raw, name_raw, phone_raw, idempotency_key,
                now: datetime | None = None) -> BookingIntent:
    """Create a hold or raise ValidationError / SlotUnavailable. Repeating a key returns the same hold."""
    cfg = current_app.config
    now = now or utcnow()

    name = _clean_name(name_raw)
    phone = normalize_ke_phone(phone_raw)
    if phone is None:
        raise ValidationError("Enter a valid Kenyan M-Pesa phone number, for example 0712 345 678.")
    key = (idempotency_key or "").strip()
    if not KEY_RE.match(key):
        raise ValidationError("This form has expired. Please reload the page and try again.")
    try:
        service_pk = int(service_id)
    except (TypeError, ValueError):
        raise SlotUnavailable("That service is not available.") from None
    start = _parse_start(start_raw)

    existing = db.session.scalar(select(BookingIntent).where(BookingIntent.idempotency_key == key))
    if existing is not None:
        if existing.business_id != business.id:
            raise ValidationError("This form has expired. Please reload the page and try again.")
        return existing

    service = catalog_service.get_bookable_service(business, service_pk)
    if service is None:
        raise SlotUnavailable("That service is not available.")

    local_day = start.astimezone(availability_service._business_tz(business)).date()
    try:
        # Serialise everything that could touch this business on this day. Released on commit/rollback.
        db.session.execute(text("SELECT pg_advisory_xact_lock(CAST(:a AS integer), CAST(:b AS integer))"),
                           {"a": business.id, "b": local_day.toordinal()})

        if not availability_service.is_slot_available(business, service, start, now=now):
            db.session.rollback()
            raise SlotUnavailable("Sorry, that time was just taken or is no longer available.")

        open_holds = db.session.scalar(
            select(func.count()).select_from(BookingIntent).where(
                BookingIntent.business_id == business.id, BookingIntent.customer_phone == phone,
                BookingIntent.status == STATUS_PENDING, BookingIntent.hold_expires_at > now)
        )
        if open_holds >= cfg["BOOKING_MAX_HOLDS_PER_PHONE"]:
            db.session.rollback()
            raise ValidationError(
                "You already have time slots on hold. Finish or release them, or wait a few minutes.")

        intent = BookingIntent(
            public_token=secrets.token_urlsafe(24), idempotency_key=key,
            business_id=business.id, service_id=service.id,
            customer_name=name, customer_phone=phone,
            service_name_snapshot=service.name, duration_minutes_snapshot=service.duration_minutes,
            amount_kes_snapshot=service.price_kes,
            fee_bps_snapshot=cfg["PAYMENT_FEE_BPS"],
            fee_kes_snapshot=payment_fee_kes(service.price_kes, cfg["PAYMENT_FEE_BPS"]),
            starts_at=start, ends_at=start + timedelta(minutes=service.duration_minutes),
            status=STATUS_PENDING, hold_expires_at=now + timedelta(minutes=cfg["BOOKING_HOLD_MINUTES"]),
        )
        db.session.add(intent)
        db.session.commit()
        return intent
    except IntegrityError:
        db.session.rollback()
        again = db.session.scalar(select(BookingIntent).where(BookingIntent.idempotency_key == key))
        if again is not None and again.business_id == business.id:
            return again  # the same form was submitted twice at once
        raise


def release_hold(intent: BookingIntent) -> bool:
    """Customer changed their mind: free the slot now. Only pending holds can be released."""
    if intent.status != STATUS_PENDING:
        return False
    intent.status = STATUS_EXPIRED
    db.session.commit()
    return True


def expire_holds(now: datetime | None = None) -> int:
    """Mark pending holds past their expiry as expired. Safe to run often (worker/cron/CLI)."""
    now = now or utcnow()
    result = db.session.execute(
        update(BookingIntent)
        .where(BookingIntent.status == STATUS_PENDING, BookingIntent.hold_expires_at <= now)
        .values(status=STATUS_EXPIRED)
    )
    db.session.commit()
    return result.rowcount
