"""Manual weekly payouts to businesses (Bucket 6). AFRIBOOK sends the money by hand, then records it here.

What a business is owed for a booking is the SERVICE PRICE only. The customer paid the payment fee on top,
which covers the provider's collection cost, so nothing is deducted from the business.
A booking becomes payable once the appointment has ended, and can be attached to at most one payout.
"""
import csv
import io
import re
from datetime import datetime

from sqlalchemy import func, select, update

from app.extensions import db
from app.models.booking import Booking
from app.models.business import Business, PaymentAccount
from app.models.payment import (ATTEMPT_REVIEW, PAYOUT_CANCELLED, PAYOUT_PAID, PAYOUT_PENDING, PaymentAttempt, Payout)
from app.services.errors import ValidationError
from app.utils.timeutil import utcnow

REFERENCE_RE = re.compile(r"^[A-Za-z0-9]{6,40}$")


def _eligible(now):
    return (Booking.payout_id.is_(None), Booking.ends_at <= now)


def owed_summary(now: datetime | None = None) -> list[dict]:
    """One row per business that has unpaid, finished appointments."""
    now = now or utcnow()
    rows = db.session.execute(
        select(Business, func.count(Booking.id), func.sum(Booking.amount_kes_snapshot))
        .join(Booking, Booking.business_id == Business.id).where(*_eligible(now))
        .group_by(Business.id).order_by(Business.name))
    return [{"business": b, "count": c, "total": int(t),
             "destination": destination_text(b)} for b, c, t in rows]


def destination_text(business: Business) -> str | None:
    acct = db.session.scalar(select(PaymentAccount).where(PaymentAccount.business_id == business.id))
    if acct is None:
        return None
    text = f"{acct.payment_type.title()} {acct.shortcode}"
    if acct.payment_type == "PAYBILL" and acct.account_reference_template:
        text += f" (account {acct.account_reference_template})"
    return text


def create_payout(business_id: int, admin_user_id: int | None, now: datetime | None = None) -> Payout:
    now = now or utcnow()
    business = db.session.get(Business, business_id)
    if business is None:
        raise ValidationError("Business not found.")
    destination = destination_text(business)
    if destination is None:
        raise ValidationError("This business has not set where to receive payouts.")
    rows = list(db.session.scalars(
        select(Booking).where(Booking.business_id == business_id, *_eligible(now)).with_for_update()))
    if not rows:
        raise ValidationError("Nothing is owed to this business yet.")
    payout = Payout(business_id=business_id, status=PAYOUT_PENDING, total_kes=sum(b.amount_kes_snapshot for b in rows),
                    booking_count=len(rows), destination_snapshot=destination[:200], created_by_user_id=admin_user_id)
    db.session.add(payout)
    db.session.flush()
    db.session.execute(update(Booking).where(Booking.id.in_([b.id for b in rows])).values(payout_id=payout.id))
    db.session.commit()
    return payout


def mark_paid(payout_id: int, reference: str, now: datetime | None = None) -> Payout:
    reference = (reference or "").strip().upper()
    if not REFERENCE_RE.match(reference):
        raise ValidationError("Enter the M-Pesa or bank reference (6-40 letters and numbers, no spaces).")
    payout = db.session.scalar(select(Payout).where(Payout.id == payout_id).with_for_update())
    if payout is None:
        raise ValidationError("Payout not found.")
    if payout.status != PAYOUT_PENDING:
        raise ValidationError(f"This payout is already {payout.status}.")
    payout.status = PAYOUT_PAID
    payout.mpesa_reference = reference
    payout.paid_at = now or utcnow()
    db.session.commit()
    return payout


def cancel_payout(payout_id: int) -> Payout:
    """Undo a payout that has not been sent: its bookings become payable again."""
    payout = db.session.scalar(select(Payout).where(Payout.id == payout_id).with_for_update())
    if payout is None:
        raise ValidationError("Payout not found.")
    if payout.status != PAYOUT_PENDING:
        raise ValidationError("Only a payout that has not been paid can be cancelled.")
    db.session.execute(update(Booking).where(Booking.payout_id == payout.id).values(payout_id=None))
    payout.status = PAYOUT_CANCELLED
    db.session.commit()
    return payout


def get_payout(payout_id: int) -> Payout | None:
    return db.session.get(Payout, payout_id)


def payout_bookings(payout_id: int) -> list[Booking]:
    return list(db.session.scalars(select(Booking).where(Booking.payout_id == payout_id).order_by(Booking.starts_at)))


def list_payouts(limit: int = 100) -> list[tuple[Payout, Business]]:
    return list(db.session.execute(
        select(Payout, Business).join(Business, Business.id == Payout.business_id)
        .order_by(Payout.id.desc()).limit(limit)).all())


def payout_csv(payout: Payout) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["booking_reference", "appointment_start_utc", "service", "customer_phone", "amount_kes"])
    for b in payout_bookings(payout.id):
        writer.writerow([b.public_reference, b.starts_at.isoformat(), b.service_name_snapshot,
                         b.customer_phone_snapshot, b.amount_kes_snapshot])
    writer.writerow([])
    writer.writerow(["TOTAL", "", "", "", payout.total_kes])
    return out.getvalue()


def review_queue() -> list[tuple[PaymentAttempt, Business]]:
    """Payments that arrived but could not be turned into a booking. Admin refunds/resolves by hand."""
    from app.models.booking import BookingIntent
    return list(db.session.execute(
        select(PaymentAttempt, Business)
        .join(BookingIntent, BookingIntent.id == PaymentAttempt.booking_intent_id)
        .join(Business, Business.id == BookingIntent.business_id)
        .where(PaymentAttempt.status == ATTEMPT_REVIEW, PaymentAttempt.resolved_at.is_(None))
        .order_by(PaymentAttempt.id)).all())


def resolve_review(attempt_id: int, note: str, now: datetime | None = None) -> PaymentAttempt:
    note = " ".join((note or "").split())
    if not (3 <= len(note) <= 200):
        raise ValidationError("Write a short note on how this was resolved (for example the refund reference).")
    attempt = db.session.get(PaymentAttempt, attempt_id)
    if attempt is None or attempt.status != ATTEMPT_REVIEW or attempt.resolved_at is not None:
        raise ValidationError("Nothing to resolve.")
    attempt.resolved_at = now or utcnow()
    attempt.resolution_note = note
    db.session.commit()
    return attempt
