from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

PURPOSE_SIGNUP = "signup_verify"
PURPOSE_RESET = "password_reset"


class PasswordResetOtp(db.Model):
    """One-time codes for signup phone verification and password reset.

    Only an HMAC of the code is stored, never the code itself.
    """

    __tablename__ = "password_reset_otps"
    __table_args__ = (
        CheckConstraint("purpose IN ('signup_verify', 'password_reset')", name="purpose_valid"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        Index("ix_password_reset_otps_lookup", "phone_e164", "purpose", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    phone_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
