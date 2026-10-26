from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.utils.slots import generate_slots, overlaps

EAT = ZoneInfo("Africa/Nairobi")
UTC = timezone.utc
DAY = date(2026, 11, 2)  # a Monday
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


def at(h, m=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=EAT)


def slots(duration=60, windows=((time(9), time(17)),), **kw):
    kw.setdefault("now", LONG_AGO)
    return generate_slots(day=DAY, duration_minutes=duration, windows=list(windows), tz=EAT, **kw)


def starts(result):
    return [s.strftime("%H:%M") for s in result]


# ---------------------------------------------------------------- basics
def test_overlap_is_half_open():
    assert overlaps(at(9), at(10), at(9, 30), at(10, 30))
    assert not overlaps(at(9), at(10), at(10), at(11))   # touching ends do not overlap
    assert not overlaps(at(10), at(11), at(9), at(10))
    assert overlaps(at(9), at(12), at(10), at(11))        # containment


def test_basic_grid_every_15_minutes():
    result = slots(60, [(time(9), time(12))])
    assert starts(result) == ["09:00", "09:15", "09:30", "09:45", "10:00", "10:15", "10:30", "10:45", "11:00"]


def test_appointment_may_end_exactly_at_closing():
    assert starts(slots(45, [(time(9), time(10))])) == ["09:00", "09:15"]


def test_service_longer_than_window_has_no_slots():
    assert slots(120, [(time(9), time(10))]) == []


def test_duration_changes_last_start():
    assert starts(slots(30, [(time(9), time(10))]))[-1] == "09:30"
    assert starts(slots(90, [(time(9), time(11))]))[-1] == "09:30"


def test_no_windows_means_closed():
    assert slots(60, []) == []


def test_multiple_windows_in_one_day():
    result = starts(slots(60, [(time(8), time(10)), (time(14), time(16))]))
    assert result == ["08:00", "08:15", "08:30", "08:45", "09:00", "14:00", "14:15", "14:30", "14:45", "15:00"]


def test_overlapping_windows_do_not_duplicate_slots():
    result = slots(60, [(time(9), time(11)), (time(10), time(12))])
    assert len(result) == len(set(result))


def test_grid_is_anchored_to_window_start():
    assert starts(slots(30, [(time(8, 10), time(9, 10))])) == ["08:10", "08:25", "08:40"]


def test_custom_increment():
    assert starts(slots(30, [(time(9), time(11))], increment_minutes=30)) == ["09:00", "09:30", "10:00", "10:30"]


@pytest.mark.parametrize("duration,increment", [(0, 15), (-5, 15), (30, 0), (30, -15)])
def test_invalid_numbers_raise(duration, increment):
    with pytest.raises(ValueError):
        slots(duration, increment_minutes=increment)


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        slots(60, now=datetime(2026, 1, 1))


# ---------------------------------------------------------------- breaks
def test_break_removes_overlapping_slots_but_allows_adjacent():
    result = starts(slots(60, [(time(9), time(17))], breaks=[(time(12), time(13))]))
    assert "11:00" in result          # ends exactly when the break starts
    assert "11:15" not in result      # would run into the break
    for blocked_start in ("12:00", "12:15", "12:30", "12:45"):
        assert blocked_start not in result
    assert "13:00" in result          # starts exactly when the break ends


def test_break_shorter_than_service_still_blocks():
    result = starts(slots(60, [(time(9), time(12))], breaks=[(time(10, 20), time(10, 40))]))
    assert result == ["09:00", "09:15", "10:45", "11:00"]


# ---------------------------------------------------------------- blocked periods
def test_blocked_period_partial_day():
    blocked = [(at(10), at(12))]
    result = starts(slots(60, [(time(8), time(14))], blocked=blocked))
    assert "09:00" in result and "09:15" not in result
    assert "12:00" in result and "11:45" not in result


def test_blocked_all_day_removes_everything():
    assert slots(60, [(time(8), time(18))], blocked=[(at(0), at(0, 0, DAY + timedelta(days=1)))]) == []


def test_blocked_period_given_in_utc_is_converted_correctly():
    # 05:00-06:00 UTC is 08:00-09:00 in Nairobi (UTC+3).
    blocked = [(datetime(2026, 11, 2, 5, tzinfo=UTC), datetime(2026, 11, 2, 6, tzinfo=UTC))]
    result = starts(slots(30, [(time(7), time(10))], blocked=blocked))
    assert result == ["07:00", "07:15", "07:30", "09:00", "09:15", "09:30"]


def test_block_that_starts_before_and_ends_inside_the_day():
    blocked = [(at(0, 0, DAY - timedelta(days=1)), at(9))]
    assert starts(slots(60, [(time(8), time(11))], blocked=blocked))[0] == "09:00"


# ------------------------------------------------------------- existing bookings
def test_busy_interval_blocks_overlap_but_allows_back_to_back():
    result = starts(slots(60, [(time(8), time(13))], busy=[(at(10), at(11))]))
    assert "09:00" in result          # ends at 10:00, booking starts at 10:00
    assert "09:15" not in result and "10:45" not in result
    assert "11:00" in result          # starts when the booking ends


def test_several_busy_intervals():
    busy = [(at(9), at(10)), (at(11), at(12))]
    assert starts(slots(60, [(time(9), time(13))], busy=busy)) == ["10:00", "12:00"]


# ----------------------------------------------------------------- notice / past
def test_slots_before_now_plus_notice_are_removed():
    now = datetime(2026, 11, 2, 4, 50, tzinfo=UTC)  # 07:50 Nairobi
    result = starts(slots(30, [(time(7), time(10))], now=now, min_notice_minutes=30))
    assert result[0] == "08:30" and "08:15" not in result  # earliest allowed is 08:20


def test_slot_exactly_at_earliest_is_allowed():
    now = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)  # 08:00 Nairobi
    assert starts(slots(30, [(time(7), time(10))], now=now, min_notice_minutes=0))[0] == "08:00"


def test_everything_in_the_past_is_removed():
    now = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)  # 23:00 Nairobi
    assert slots(30, [(time(7), time(18))], now=now) == []


# -------------------------------------------------------------------- timezone
def test_slots_are_nairobi_aware_and_convert_to_utc():
    first = slots(60, [(time(9), time(10))])[0]
    assert first.utcoffset() == timedelta(hours=3)
    assert first.astimezone(UTC) == datetime(2026, 11, 2, 6, 0, tzinfo=UTC)


def test_early_morning_slot_falls_on_previous_utc_day():
    first = slots(30, [(time(1), time(2))])[0]  # 01:00 Nairobi = 22:00 UTC the day before
    assert first.astimezone(UTC).date() == date(2026, 11, 1)
