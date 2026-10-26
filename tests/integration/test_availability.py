from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.availability import BlockedPeriod, WeeklyAvailability, WeeklyBreak
from app.models.service import Service
from app.services import availability_service as av
from tests.integration.test_business import OTHER_E164, OTHER_PHONE
from tests.integration.test_services import add_service, owner_with_business
from tests.helpers import make_user, login

UTC = timezone.utc
MON = 0


def hours_form(days, start="09:00", end="17:00"):
    return {"days": [str(d) for d in days], "start_time": start, "end_time": end}


def rows(app, model):
    with app.app_context():
        out = list(db.session.scalars(select(model).order_by(model.id)))
        for r in out:
            db.session.expunge(r)
        return out


def setup_owner(app, **kw):
    client = owner_with_business(app, **kw)
    add_service(client, name="Haircut", duration_minutes="60")
    return client


def future_monday(weeks=1):
    d = date.today() + timedelta(days=7 * weeks)
    return d - timedelta(days=d.weekday())


def slots_for(app, day, now=None, slug="glow-salon"):
    with app.app_context():
        business = get_business_obj(slug)
        service = db.session.scalar(select(Service).where(Service.business_id == business.id))
        return [s.astimezone(av._business_tz(business)).strftime("%H:%M")
                for s in av.get_slots_for_day(business, service, day, now=now)]


def get_business_obj(slug):
    from app.models.business import Business
    return db.session.scalar(select(Business).where(Business.slug == slug))


# ------------------------------------------------------------------ access
def test_requires_login_and_owner_role(app):
    anon = app.test_client()
    for path in ("/availability", "/blocked-periods"):
        assert anon.get(path).status_code == 302
    assert anon.post("/availability/hours", data=hours_form([0])).status_code == 302
    make_user(app, phone="+254733000111", role="admin")
    admin = app.test_client()
    login(admin, phone="0733000111")
    assert admin.get("/availability").status_code == 403
    assert admin.post("/availability/hours", data=hours_form([0])).status_code == 403


def test_requires_business_first(app):
    from tests.integration.test_business import owner_client
    client = owner_client(app)
    for path in ("/availability", "/blocked-periods"):
        resp = client.get(path)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/settings/profile")
    assert rows(app, WeeklyAvailability) == []


# ------------------------------------------------------------- weekly hours
def test_add_hours_for_several_days_at_once(app):
    client = setup_owner(app)
    resp = client.post("/availability/hours", data=hours_form([0, 1, 2, 3, 4]))
    assert resp.status_code == 302
    saved = rows(app, WeeklyAvailability)
    assert sorted(r.weekday for r in saved) == [0, 1, 2, 3, 4]
    assert all(r.start_time == time(9) and r.end_time == time(17) for r in saved)
    assert b"Monday" in client.get("/availability").data


@pytest.mark.parametrize("data", [
    {"start_time": "09:00", "end_time": "17:00"},                     # no days
    hours_form([0], start="17:00", end="09:00"),                      # reversed
    hours_form([0], start="09:00", end="09:00"),                      # empty
    hours_form([0], start="abc", end="17:00"),                        # junk
    hours_form([9], start="09:00", end="17:00"),                      # bad weekday
])
def test_invalid_hours_rejected(app, data):
    client = setup_owner(app)
    client.post("/availability/hours", data=data)
    assert rows(app, WeeklyAvailability) == []


def test_overlap_rejected_and_nothing_partial_saved(app):
    client = setup_owner(app)
    client.post("/availability/hours", data=hours_form([1], "09:00", "12:00"))
    client.post("/availability/hours", data=hours_form([0, 1], "11:00", "13:00"))  # Tuesday overlaps
    saved = rows(app, WeeklyAvailability)
    assert [(r.weekday, r.start_time) for r in saved] == [(1, time(9))]  # Monday not added either


def test_touching_ranges_allowed_and_split_day(app):
    client = setup_owner(app)
    client.post("/availability/hours", data=hours_form([0], "08:00", "12:00"))
    client.post("/availability/hours", data=hours_form([0], "12:00", "18:00"))
    assert len(rows(app, WeeklyAvailability)) == 2


def test_max_windows_per_day(app):
    client = setup_owner(app)
    for i in range(4):
        client.post("/availability/hours", data=hours_form([0], f"{8 + 2 * i:02d}:00", f"{9 + 2 * i:02d}:00"))
    client.post("/availability/hours", data=hours_form([0], "20:00", "21:00"))
    assert len(rows(app, WeeklyAvailability)) == 4


def test_delete_hours_and_cross_owner_is_404(app):
    a = setup_owner(app)
    a.post("/availability/hours", data=hours_form([0]))
    entry_id = rows(app, WeeklyAvailability)[0].id
    b = setup_owner(app, phone=OTHER_PHONE, e164=OTHER_E164, slug="other-biz")
    assert b.post(f"/availability/hours/{entry_id}/delete").status_code == 404
    assert len(rows(app, WeeklyAvailability)) == 1
    assert a.post(f"/availability/hours/{entry_id}/delete").status_code == 302
    assert rows(app, WeeklyAvailability) == []


def test_breaks_add_and_delete(app):
    client = setup_owner(app)
    client.post("/availability/breaks", data=hours_form([0, 1], "13:00", "14:00"))
    brk = rows(app, WeeklyBreak)
    assert len(brk) == 2
    client.post(f"/availability/breaks/{brk[0].id}/delete")
    assert len(rows(app, WeeklyBreak)) == 1
    assert client.post("/availability/breaks/99999/delete").status_code == 404


def test_public_page_shows_opening_hours_and_closed_days(app):
    client = setup_owner(app)
    client.post("/availability/hours", data=hours_form([0], "09:00", "17:00"))
    html = app.test_client().get("/glow-salon").data.decode()
    assert "Opening hours" in html and "09:00 - 17:00" in html and "Closed" in html


def test_dashboard_counts_open_days(app):
    client = setup_owner(app)
    client.post("/availability/hours", data=hours_form([0, 1, 2]))
    assert b"3 days open per week" in client.get("/dashboard").data


# ----------------------------------------------------------- blocked periods
def tomorrow():
    return (datetime.now(UTC) + timedelta(days=2)).date()


def test_block_whole_days_inclusive(app):
    client = setup_owner(app)
    d = tomorrow()
    resp = client.post("/blocked-periods", data={
        "start_date": d.isoformat(), "end_date": (d + timedelta(days=1)).isoformat(),
        "all_day": "on", "reason": "Holiday"})
    assert resp.status_code == 302
    (p,) = rows(app, BlockedPeriod)
    assert p.ends_at - p.starts_at == timedelta(days=2)
    assert p.reason == "Holiday"
    assert b"Holiday" in client.get("/blocked-periods").data


def test_block_time_range_single_day(app):
    client = setup_owner(app)
    d = tomorrow().isoformat()
    client.post("/blocked-periods", data={"start_date": d, "start_time": "10:00", "end_time": "12:00"})
    (p,) = rows(app, BlockedPeriod)
    assert p.ends_at - p.starts_at == timedelta(hours=2)


@pytest.mark.parametrize("data", [
    {"start_date": "", "all_day": "on"},
    {"start_date": "2020-01-01", "all_day": "on"},                              # past
    {"start_date": "2099-01-02", "end_date": "2099-01-01", "all_day": "on"},   # reversed
    {"start_date": "2099-01-01", "start_time": "12:00", "end_time": "10:00"},  # reversed times
    {"start_date": "2099-01-01"},                                              # times required
    {"start_date": "2099-01-01", "end_date": "2101-01-01", "all_day": "on"},   # > 366 days
    {"start_date": "2099-01-01", "all_day": "on", "reason": "x" * 201},
])
def test_invalid_blocked_period_rejected(app, data):
    client = setup_owner(app)
    resp = client.post("/blocked-periods", data=data)
    assert resp.status_code == 400
    assert rows(app, BlockedPeriod) == []


def test_delete_blocked_period_cross_owner_404(app):
    a = setup_owner(app)
    a.post("/blocked-periods", data={"start_date": tomorrow().isoformat(), "all_day": "on"})
    pid = rows(app, BlockedPeriod)[0].id
    b = setup_owner(app, phone=OTHER_PHONE, e164=OTHER_E164, slug="other-biz")
    assert b.post(f"/blocked-periods/{pid}/delete").status_code == 404
    assert a.post(f"/blocked-periods/{pid}/delete").status_code == 302
    assert rows(app, BlockedPeriod) == []


# ------------------------------------------------------------- slot lookup
def test_slots_respect_hours_break_and_blocked(app):
    client = setup_owner(app)
    client.post("/availability/hours", data=hours_form([MON], "09:00", "13:00"))
    client.post("/availability/breaks", data=hours_form([MON], "11:00", "11:30"))
    monday = future_monday()
    now = datetime.combine(monday - timedelta(days=3), time(8), tzinfo=UTC)
    # 60-minute service, 15-minute grid, break 11:00-11:30 -> last start before break 10:00, next 11:30
    assert slots_for(app, monday, now=now) == (
        ["09:00", "09:15", "09:30", "09:45", "10:00", "11:30", "11:45", "12:00"])
    client.post("/blocked-periods", data={
        "start_date": monday.isoformat(), "start_time": "09:00", "end_time": "10:00"})
    # blocked-period creation needs a future date; the helper date is always in the future
    assert slots_for(app, monday, now=now)[0] == "10:00"


def test_closed_day_has_no_slots(app):
    setup_owner(app)
    assert slots_for(app, future_monday()) == []


def test_past_and_beyond_horizon_return_nothing(app):
    client = setup_owner(app)
    client.post("/availability/hours", data=hours_form(range(7)))
    now = datetime.now(UTC)
    today = now.astimezone(av.ZoneInfo("Africa/Nairobi")).date()
    assert slots_for(app, today - timedelta(days=1), now=now) == []
    assert slots_for(app, today + timedelta(days=61), now=now) == []
    assert slots_for(app, today + timedelta(days=30), now=now) != []


def test_local_date_boundary_uses_business_timezone(app):
    client = setup_owner(app)
    client.post("/availability/hours", data=hours_form(range(7)))
    # 2026-11-01 22:00 UTC is 2026-11-02 01:00 in Nairobi, so Nov 1 is already "yesterday"
    now = datetime(2026, 11, 1, 22, 0, tzinfo=UTC)
    assert slots_for(app, date(2026, 11, 1), now=now) == []
    assert slots_for(app, date(2026, 11, 2), now=now) != []


def test_is_slot_available_matches_generated_slots(app):
    client = setup_owner(app)
    client.post("/availability/hours", data=hours_form([MON], "09:00", "12:00"))
    monday = future_monday()
    now = datetime.combine(monday - timedelta(days=3), time(8), tzinfo=UTC)
    nairobi = av.ZoneInfo("Africa/Nairobi")
    with app.app_context():
        business = get_business_obj("glow-salon")
        service = db.session.scalar(select(Service).where(Service.business_id == business.id))
        ok = datetime.combine(monday, time(10), tzinfo=nairobi)
        late = datetime.combine(monday, time(11, 30), tzinfo=nairobi)   # 60 min would end 12:30
        off_grid = datetime.combine(monday, time(10, 7), tzinfo=nairobi)
        assert av.is_slot_available(business, service, ok, now=now)
        assert not av.is_slot_available(business, service, late, now=now)
        assert not av.is_slot_available(business, service, off_grid, now=now)
        assert not av.is_slot_available(business, service, datetime(2030, 1, 1, 10), now=now)  # naive


def test_preview_page(app):
    client = setup_owner(app)
    client.post("/availability/hours", data=hours_form(range(7), "09:00", "11:00"))
    with app.app_context():
        sid = db.session.scalar(select(Service.id))
    day = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
    html = client.get(f"/availability?service_id={sid}&date={day}").data.decode()
    assert "Morning" in html and "9:00 AM" in html
    assert b"valid date" in client.get(f"/availability?service_id={sid}&date=nope").data


def test_preview_cannot_use_other_owners_service(app):
    a = setup_owner(app)
    with app.app_context():
        sid = db.session.scalar(select(Service.id))
    b = setup_owner(app, phone=OTHER_PHONE, e164=OTHER_E164, slug="other-biz")
    day = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
    assert b"Morning" not in b.get(f"/availability?service_id={sid}&date={day}").data


# ------------------------------------------------------------- DB constraints
def test_db_rejects_bad_rows(app):
    setup_owner(app)
    with app.app_context():
        bid = get_business_obj("glow-salon").id
        db.session.add(WeeklyAvailability(business_id=bid, weekday=7, start_time=time(9), end_time=time(10)))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        db.session.add(WeeklyBreak(business_id=bid, weekday=1, start_time=time(10), end_time=time(10)))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        t = datetime(2030, 1, 1, 9, tzinfo=UTC)
        db.session.add(BlockedPeriod(business_id=bid, starts_at=t, ends_at=t))
        with pytest.raises(IntegrityError):
            db.session.commit()


def test_times_display_with_am_pm(app):
    client = setup_owner(app)
    client.post("/availability/hours", data=hours_form([0], "08:00", "18:30"))
    html = client.get("/availability").data.decode()
    assert "8:00 AM - 6:30 PM" in html
    assert "8:00 AM - 6:30 PM" in app.test_client().get("/glow-salon").data.decode()
    assert "AM" in html and "PM" in html  # pickers offer both


def test_several_breaks_on_the_same_day(app):
    client = setup_owner(app)
    client.post("/availability/breaks", data=hours_form([0, 1], "10:00", "10:15"))
    client.post("/availability/breaks", data=hours_form([0, 1], "13:00", "14:00"))
    client.post("/availability/breaks", data=hours_form([0, 1], "16:00", "16:30"))
    assert len(rows(app, WeeklyBreak)) == 6
    # overlapping one is refused with a readable message and nothing partial saved
    client.post("/availability/breaks", data=hours_form([0, 2], "13:30", "14:30"))
    assert len(rows(app, WeeklyBreak)) == 6
    page = client.get("/availability").data.decode()
    assert "Nothing was saved" in page and "1:00 PM - 2:00 PM" in page
