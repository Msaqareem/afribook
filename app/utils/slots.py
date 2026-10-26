"""Pure slot-generation engine (no database, no Flask), so it is easy to test exhaustively.

Rules (Document 2, section 6.1):
  * candidate starts step from the opening-window start in `increment_minutes` steps;
  * the whole appointment [start, start + duration) must fit inside one opening window;
  * a candidate is rejected if it overlaps a recurring break, a blocked period, an existing
    booking / unexpired hold ("busy"), or starts before now + minimum notice;
  * intervals are half-open, so an appointment may end exactly when the next one starts.
"""
from datetime import date, datetime, time, timedelta, tzinfo

Interval = tuple[datetime, datetime]


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Half-open interval overlap: [a_start, a_end) vs [b_start, b_end)."""
    return a_start < b_end and b_start < a_end


def local_interval(day: date, start: time, end: time, tz: tzinfo) -> Interval:
    return datetime.combine(day, start, tzinfo=tz), datetime.combine(day, end, tzinfo=tz)


def generate_slots(
    *,
    day: date,
    duration_minutes: int,
    windows: list[tuple[time, time]],
    now: datetime,
    tz: tzinfo,
    breaks: list[tuple[time, time]] = (),
    blocked: list[Interval] = (),
    busy: list[Interval] = (),
    increment_minutes: int = 15,
    min_notice_minutes: int = 0,
) -> list[datetime]:
    """Return the start times (timezone-aware, in `tz`) a customer could book on `day`.

    `windows`/`breaks` are local wall-clock (start, end) pairs for that weekday.
    `blocked`/`busy` are timezone-aware datetime intervals (any timezone, e.g. UTC from the DB).
    `now` must be timezone-aware.
    """
    if duration_minutes <= 0 or increment_minutes <= 0:
        raise ValueError("duration and increment must be positive")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=increment_minutes)
    earliest = now + timedelta(minutes=min_notice_minutes)

    unavailable: list[Interval] = [local_interval(day, s, e, tz) for s, e in breaks]
    unavailable += list(blocked)
    unavailable += list(busy)

    slots: set[datetime] = set()
    for w_start, w_end in windows:
        start, end = local_interval(day, w_start, w_end, tz)
        t = start
        while t + duration <= end:
            if t >= earliest and not any(overlaps(t, t + duration, a, b) for a, b in unavailable):
                slots.add(t)
            t += step
    return sorted(slots)
