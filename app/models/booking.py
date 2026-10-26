from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

STATUS_PENDING = "pending_payment"
STATUS_PAID = "paid"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
STATUS_REVIEW = "review"
INTENT_STATUSES = (STATUS_PENDING, STATUS_PAID, STATUS_FAILED, STATUS_EXPIRED, STATUS_REVIEW)


class BookingIntent(db.Model):
    """A temporary reservation (hold) made before payment. Service details are snapshotted."""

    __tablename__ = "booking_intents"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="period_order"),
        CheckConstraint("amount_kes_snapshot > 0", name="amount_positive"),
        CheckConstraint("duration_minutes_snapshot > 0", name="duration_positive"),
        CheckConstraint("fee_kes_snapshot >= 0", name="fee_not_negative"),
        CheckConstraint(
            "status IN ('pending_payment','paid','failed','expired','review')", name="status_valid"
        ),
        Index("ix_booking_intents_business_start", "business_id", "starts_at", "status", "hold_expires_at"),
        Index("ix_booking_intents_status_hold", "status", "hold_expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(80), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(16), nullable=False)
    service_name_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    duration_minutes_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_kes_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)  # service price (what the business earns)
    fee_kes_snapshot: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")  # customer payment fee
    fee_bps_snapshot: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_PENDING)
    hold_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def total_kes(self) -> int:
        """What the customer pays: service price + payment fee."""
        return self.amount_kes_snapshot + self.fee_kes_snapshot


BOOKING_CONFIRMED = "confirmed"
BOOKING_COMPLETED = "completed"


class Booking(db.Model):
    """A confirmed appointment. Created ONLY from a verified successful payment."""

    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="period_order"),
        CheckConstraint("status IN ('confirmed','completed')", name="status_valid"),
        CheckConstraint("amount_kes_snapshot > 0 AND fee_kes_snapshot >= 0", name="amounts_valid"),
        Index("ix_bookings_business_start", "business_id", "starts_at", "status"),
        Index("ix_bookings_payout", "payout_id"),
        # Also in migration 0007: EXCLUDE USING gist (business_id =, tstzrange(starts_at, ends_at) &&)
        # so two confirmed bookings can never overlap, even if application code is wrong.
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_reference: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    booking_intent_id: Mapped[int] = mapped_column(
        ForeignKey("booking_intents.id", ondelete="RESTRICT"), unique=True, nullable=False)
    payment_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), unique=True, nullable=False)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"), nullable=False)
    customer_name_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    customer_phone_snapshot: Mapped[str] = mapped_column(String(16), nullable=False)
    service_name_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    duration_minutes_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_kes_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)   # service price: owed to the business
    fee_kes_snapshot: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # customer paid this on top
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=BOOKING_CONFIRMED)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    payout_id: Mapped[Optional[int]] = mapped_column(ForeignKey("payouts.id", ondelete="RESTRICT"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
