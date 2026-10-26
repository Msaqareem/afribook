"""Opening hours, breaks, blocked periods and slot lookup for a business."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models.availability import BlockedPeriod, WeeklyAvailability, WeeklyBreak
from app.models.business import Business
from app.models.service import Service
from app.services.errors import ValidationError
from app.utils.slots import generate_slots
from app.utils.timeutil import utcnow

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MAX_WINDOWS_PER_DAY = 4
MAX_BREAKS_PER_DAY = 8
MAX_BLOCK_DAYS = 366
MAX_UPCOMING_BLOCKS = 100

_KINDS = {
    "hours": (WeeklyAvailability, MAX_WINDOWS_PER_DAY, "opening hours"),
    "break": (WeeklyBreak, MAX_BREAKS_PER_DAY, "breaks"),
}


# --------------------------------------------------------------------- parsing
def _parse_time(raw, label: str) -> time:
    try:
        return datetime.strptime((raw or "").strip(), "%H:%M").time()
    except ValueError:
        raise ValidationError(f"{label} must be a valid time.") from None


def _parse_date(raw, label: str) -> date:
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(f"{label} must be a valid date.") from None


def _business_tz(business: Business) -> ZoneInfo:
    return ZoneInfo(business.timezone)


# ---------------------------------------------------------------- weekly rules
def list_weekly(business_id: int):
    """Return (windows_by_day, breaks_by_day): dict weekday -> sorted rows."""
    def group(model):
        rows = db.session.scalars(
            select(model).where(model.business_id == business_id).order_by(model.weekday, model.start_time)
        )
        out = {d: [] for d in range(7)}
        for row in rows:
            out[row.weekday].append(row)
        return out

    return group(WeeklyAvailability), group(WeeklyBreak)


def weekly_summary(business_id: int) -> list[tuple[str, list[WeeklyAvailability]]]:
    """For the public page: [(weekday name, [windows])], Monday first. Empty list = closed."""
    windows, _ = list_weekly(business_id)
    return [(WEEKDAYS[d], windows[d]) for d in range(7)]


def add_weekly_entries(business: Business, form, kind: str) -> int:
    """Add the same time range to every ticked weekday. All-or-nothing. Returns rows added."""
    model, max_per_day, label = _KINDS[kind]
    days = sorted({int(d) for d in form.getlist("days") if d.isdigit() and 0 <= int(d) <= 6})
    if not days:
        raise ValidationError("Choose at least one day.")
    start = _parse_time(form.get("start_time"), "Start time")
    end = _parse_time(form.get("end_time"), "End time")
    if end <= start:
        raise ValidationError("End time must be after start time.")

    existing = list(db.session.scalars(
        select(model).where(model.business_id == business.id, model.weekday.in_(days))
    ))
    for day in days:
        rows = [r for r in existing if r.weekday == day]
        if len(rows) >= max_per_day:
            raise ValidationError(f"{WEEKDAYS[day]} already has the maximum of {max_per_day} {label}.")
        for row in rows:
            if start < row.end_time and row.start_time < end:
                from app.utils.format import hhmm
                what = "a break" if kind == "break" else "opening hours"
                raise ValidationError(
                    f"Nothing was saved. {WEEKDAYS[day]} already has {what} {hhmm(row.start_time)} - "
                    f"{hhmm(row.end_time)}, which overlaps. Pick a different time."
                )

    for day in days:
        db.session.add(model(business_id=business.id, weekday=day, start_time=start, end_time=end))
    db.session.commit()
    return len(days)


def delete_weekly_entry(business: Business, entry_id: int, kind: str) -> bool:
    model, _, _ = _KINDS[kind]
    row = db.session.scalar(select(model).where(model.id == entry_id, model.business_id == business.id))
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


# ------------------------------------------------------------ blocked periods
def list_upcoming_blocked(business_id: int, now: datetime | None = None) -> list[BlockedPeriod]:
    now = now or utcnow()
    return list(db.session.scalars(
        select(BlockedPeriod)
        .where(BlockedPeriod.business_id == business_id, BlockedPeriod.ends_at > now)
        .order_by(BlockedPeriod.starts_at).limit(MAX_UPCOMING_BLOCKS)
    ))


def add_blocked_period(business: Business, form, now: datetime | None = None) -> BlockedPeriod:
    """Dates and times are entered in the business's local time and stored as UTC instants."""
    tz = _business_tz(business)
    start_date = _parse_date(form.get("start_date"), "Start date")
    end_date = _parse_date(form.get("end_date") or form.get("start_date"), "End date")
    if end_date < start_date:
        raise ValidationError("End date cannot be before the start date.")

    if form.get("all_day") == "on":
        starts = datetime.combine(start_date, time.min, tzinfo=tz)
        ends = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz)  # end date inclusive
    else:
        start_time = _parse_time(form.get("start_time"), "Start time")
        end_time = _parse_time(form.get("end_time"), "End time")
        starts = datetime.combine(start_date, start_time, tzinfo=tz)
        ends = datetime.combine(end_date, end_time, tzinfo=tz)
        if ends <= starts:
            raise ValidationError("The end must be after the start.")

    if ends <= (now or utcnow()):
        raise ValidationError("That period is already over. Choose a future date.")
    if ends - starts > timedelta(days=MAX_BLOCK_DAYS):
        raise ValidationError(f"A blocked period can be at most {MAX_BLOCK_DAYS} days.")

    reason = " ".join((form.get("reason") or "").split()) or None
    if reason and len(reason) > 200:
        raise ValidationError("Reason must be 200 characters or fewer.")

    period = BlockedPeriod(business_id=business.id, starts_at=starts, ends_at=ends, reason=reason)
    db.session.add(period)
    db.session.commit()
    return period


def delete_blocked_period(business: Business, period_id: int) -> bool:
    row = db.session.scalar(
        select(BlockedPeriod).where(BlockedPeriod.id == period_id, BlockedPeriod.business_id == business.id)
    )
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


# ------------------------------------------------------------------ slot lookup
def busy_intervals(business_id: int, day_start: datetime, day_end: datetime,
                   now: datetime | None = None, exclude_intent_id: int | None = None
                   ) -> list[tuple[datetime, datetime]]:
    """Time that is already taken inside [day_start, day_end): confirmed bookings, paid intents and
    unexpired payment holds. `exclude_intent_id` lets a payment ignore its own hold when it is confirmed."""
    from app.models.booking import Booking, BookingIntent, STATUS_PAID, STATUS_PENDING

    now = now or utcnow()
    intents = select(BookingIntent.starts_at, BookingIntent.ends_at).where(
        BookingIntent.business_id == business_id,
        BookingIntent.starts_at < day_end, BookingIntent.ends_at > day_start,
        (BookingIntent.status == STATUS_PAID)
        | ((BookingIntent.status == STATUS_PENDING) & (BookingIntent.hold_expires_at > now)),
    )
    bookings = select(Booking.starts_at, Booking.ends_at, Booking.booking_intent_id).where(
        Booking.business_id == business_id, Booking.starts_at < day_end, Booking.ends_at > day_start)
    if exclude_intent_id is not None:
        intents = intents.where(BookingIntent.id != exclude_intent_id)
        bookings = bookings.where(Booking.booking_intent_id != exclude_intent_id)
    out = [(a, b) for a, b in db.session.execute(intents)]
    out += [(a, b) for a, b, _ in db.session.execute(bookings)]
    return out


def get_slots_for_day(business: Business, service: Service, day: date, now: datetime | None = None) -> list[datetime]:
    """Start times a customer could book for `service` on `day` (business-local date)."""
    cfg = current_app.config
    now = now or utcnow()
    tz = _business_tz(business)
    today = now.astimezone(tz).date()
    if day < today or day > today + timedelta(days=cfg["BOOKING_MAX_ADVANCE_DAYS"]):
        return []

    windows = list(db.session.scalars(
        select(WeeklyAvailability).where(
            WeeklyAvailability.business_id == business.id, WeeklyAvailability.weekday == day.weekday())
    ))
    breaks = list(db.session.scalars(
        select(WeeklyBreak).where(WeeklyBreak.business_id == business.id, WeeklyBreak.weekday == day.weekday())
    ))
    day_start = datetime.combine(day, time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    blocked = list(db.session.scalars(
        select(BlockedPeriod).where(
            BlockedPeriod.business_id == business.id,
            BlockedPeriod.starts_at < day_end, BlockedPeriod.ends_at > day_start)
    ))

    return generate_slots(
        day=day,
        duration_minutes=service.duration_minutes,
        windows=[(w.start_time, w.end_time) for w in windows],
        breaks=[(b.start_time, b.end_time) for b in breaks],
        blocked=[(b.starts_at, b.ends_at) for b in blocked],
        busy=busy_intervals(business.id, day_start, day_end, now),
        now=now,
        tz=tz,
        increment_minutes=cfg["BOOKING_SLOT_INCREMENT_MINUTES"],
        min_notice_minutes=cfg["BOOKING_MIN_NOTICE_MINUTES"],
    )


def is_slot_available(business: Business, service: Service, start: datetime, now: datetime | None = None) -> bool:
    """Server-side recheck used at booking time (Bucket 5): is `start` one of today's valid slots?"""
    if start.tzinfo is None:
        return False
    local_day = start.astimezone(_business_tz(business)).date()
    return start in get_slots_for_day(business, service, local_day, now=now)
