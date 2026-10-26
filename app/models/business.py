from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

BUSINESS_ACTIVE = "active"
BUSINESS_SUSPENDED = "suspended"

PAYMENT_TILL = "TILL"
PAYMENT_PAYBILL = "PAYBILL"


class Category(db.Model):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sa.true())
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=sa.text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Business(db.Model):
    __tablename__ = "businesses"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended')", name="status_valid"),
        CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug) BETWEEN 3 AND 40",
            name="slug_format",
        ),
        Index("ix_businesses_directory", "category_id", "county", "is_public", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(16), nullable=False)
    county: Mapped[str] = mapped_column(String(40), nullable=False)
    address: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    logo_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sa.true())
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=BUSINESS_ACTIVE, server_default=sa.text("'active'")
    )
    timezone: Mapped[str] = mapped_column(
        String(40), nullable=False, default="Africa/Nairobi", server_default=sa.text("'Africa/Nairobi'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    category: Mapped["Category"] = relationship()
    payment_account: Mapped[Optional["PaymentAccount"]] = relationship(
        back_populates="business", uselist=False
    )

    @property
    def is_listed(self) -> bool:
        """Visible to the public (directory and booking page)."""
        return self.is_public and self.status == BUSINESS_ACTIVE


class PaymentAccount(db.Model):
    """Where a business wants to be paid. Stored configuration only; no live M-Pesa use yet."""

    __tablename__ = "payment_accounts"
    __table_args__ = (
        CheckConstraint("payment_type IN ('TILL', 'PAYBILL')", name="type_valid"),
        CheckConstraint("status IN ('pending', 'verified', 'disabled')", name="status_valid"),
        CheckConstraint("shortcode ~ '^[0-9]{5,7}$'", name="shortcode_format"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    payment_type: Mapped[str] = mapped_column(String(8), nullable=False)
    shortcode: Mapped[str] = mapped_column(String(10), nullable=False)
    account_reference_template: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=sa.text("'pending'")
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    business: Mapped["Business"] = relationship(back_populates="payment_account")
