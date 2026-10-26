from datetime import datetime, time
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Time, func
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class WeeklyAvailability(db.Model):
    """One opening window on one weekday (0 = Monday ... 6 = Sunday). No row means closed."""

    __tablename__ = "weekly_availability"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_valid"),
        CheckConstraint("end_time > start_time", name="time_order"),
        Index("ix_weekly_availability_business_day", "business_id", "weekday"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WeeklyBreak(db.Model):
    """A recurring break (e.g. lunch) removed from selectable slots."""

    __tablename__ = "weekly_breaks"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_valid"),
        CheckConstraint("end_time > start_time", name="time_order"),
        Index("ix_weekly_breaks_business_day", "business_id", "weekday"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)


class BlockedPeriod(db.Model):
    """A date-specific closure (holiday, leave, maintenance), stored as UTC instants."""

    __tablename__ = "blocked_periods"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="period_order"),
        Index("ix_blocked_periods_business_start", "business_id", "starts_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
