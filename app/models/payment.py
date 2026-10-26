from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from sqlalchemy import func

ATTEMPT_INITIATED = "initiated"   # row created, provider not yet called
ATTEMPT_PENDING = "pending"       # M-Pesa prompt sent, waiting for the customer
ATTEMPT_SUCCEEDED = "succeeded"   # verified successful payment; booking created
ATTEMPT_FAILED = "failed"         # provider reported failure (wrong PIN, no funds, ...)
ATTEMPT_CANCELLED = "cancelled"   # customer dismissed the prompt
ATTEMPT_TIMEOUT = "timeout"       # no answer before the hold ended
ATTEMPT_REVIEW = "review"         # money arrived but we cannot honour it automatically
ATTEMPT_STATUSES = (ATTEMPT_INITIATED, ATTEMPT_PENDING, ATTEMPT_SUCCEEDED, ATTEMPT_FAILED,
                    ATTEMPT_CANCELLED, ATTEMPT_TIMEOUT, ATTEMPT_REVIEW)
ATTEMPT_ACTIVE = (ATTEMPT_INITIATED, ATTEMPT_PENDING)

PAYOUT_PENDING = "pending"
PAYOUT_PAID = "paid"
PAYOUT_CANCELLED = "cancelled"


class PaymentAttempt(db.Model):
    """One request to charge a customer's phone. Never stores a PIN or any credential."""

    __tablename__ = "payment_attempts"
    __table_args__ = (
        CheckConstraint("amount_kes > 0", name="amount_positive"),
        CheckConstraint(
            "status IN ('initiated','pending','succeeded','failed','cancelled','timeout','review')",
            name="status_valid",
        ),
        # At most one live prompt per booking intent: prevents double STK pushes / double charges.
        Index("uq_payment_attempts_one_active", "booking_intent_id", unique=True,
              postgresql_where=text("status IN ('initiated','pending')")),
        Index("ix_payment_attempts_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_intent_id: Mapped[int] = mapped_column(
        ForeignKey("booking_intents.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    our_reference: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)   # sent to provider as api_ref
    provider_reference: Mapped[Optional[str]] = mapped_column(String(80), unique=True, nullable=True)
    amount_kes: Mapped[int] = mapped_column(Integer, nullable=False)                      # total the customer pays
    customer_phone: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=ATTEMPT_INITIATED)
    result_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    result_description: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    mpesa_receipt: Mapped[Optional[str]] = mapped_column(String(40), unique=True, nullable=True)
    callback_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    initiated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    callback_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)   # admin closed a `review`
    resolution_note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Payout(db.Model):
    """Money AFRIBOOK sends to a business for completed appointments (manual in Bucket 6)."""

    __tablename__ = "payouts"
    __table_args__ = (
        CheckConstraint("status IN ('pending','paid','cancelled')", name="status_valid"),
        CheckConstraint("total_kes >= 0 AND booking_count >= 0", name="amounts_valid"),
        CheckConstraint("status <> 'paid' OR (paid_at IS NOT NULL AND mpesa_reference IS NOT NULL)", name="paid_has_proof"),
        Index("ix_payouts_business_status", "business_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=PAYOUT_PENDING)
    total_kes: Mapped[int] = mapped_column(Integer, nullable=False)
    booking_count: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    mpesa_reference: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
