import threading
from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.booking import BookingIntent
from app.models.business import Business
from app.models.service import Service
from app.services import availability_service as av
from app.services import booking_service
from app.services.errors import ValidationError
from app.utils.timeutil import NAIROBI, utcnow
from tests.integration.test_availability import hours_form, setup_owner
from tests.integration.test_business import OTHER_E164, OTHER_PHONE

UTC = timezone.utc
SLUG = "glow-salon"


def local_day(days_ahead=3):
    return (utcnow().astimezone(NAIROBI) + timedelta(days=days_ahead)).date()


def ts(hour=10, minute=0, days_ahead=3):
    return int(datetime.combine(local_day(days_ahead), time(hour, minute), tzinfo=NAIROBI).timestamp())


def open_shop(app):
    client = setup_owner(app)
    client.post("/availability/hours", data=hours_form(range(7), "09:00", "17:00"))
    return client


def intents(app):
    with app.app_context():
        out = list(db.session.scalars(select(BookingIntent).order_by(BookingIntent.id)))
        for r in out:
            db.session.expunge(r)
        return out


def service_id(app, slug=SLUG):
    with app.app_context():
        return db.session.scalar(select(Service.id).join(Business).where(Business.slug == slug))


def form(app, start=None, key="key-aaaaaaaaaa", name="Wanjiku Kamau", phone="0722 111 222", slug=SLUG, **kw):
    data = {"slug": slug, "service_id": str(service_id(app, slug)), "start": str(start or ts()),
            "name": name, "phone": phone, "idempotency_key": key}
    data.update(kw)
    return data


def hold(app, **kw):
    return app.test_client().post("/booking-intents", data=form(app, **kw))


def slot_labels(app, days_ahead=3):
    html = app.test_client().get(
        f"/{SLUG}/slots?service_id={service_id(app)}&date={local_day(days_ahead).isoformat()}").data.decode()
    return html


# ------------------------------------------------------------ public pages
def test_public_page_has_book_button(app):
    open_shop(app)
    html = app.test_client().get(f"/{SLUG}").data.decode()
    assert f"/{SLUG}/slots?service_id={service_id(app)}" in html and "Book" in html


def test_slots_page_lists_times_in_am_pm_groups(app):
    open_shop(app)
    html = slot_labels(app)
    assert "Morning" in html and "Afternoon" in html and "9:00 AM" in html and "3:45 PM" in html
    assert "4:15 PM" not in html  # 60-minute service must end by 5:00 PM


def test_slots_page_for_closed_day(app):
    setup_owner(app)
    assert "No times available" in slot_labels(app)


def test_slots_unknown_or_inactive_service_is_404(app):
    client = open_shop(app)
    anon = app.test_client()
    assert anon.get(f"/{SLUG}/slots?service_id=999999").status_code == 404
    assert anon.get(f"/{SLUG}/slots?service_id=abc").status_code == 404
    sid = service_id(app)
    client.post(f"/services/{sid}/toggle")
    assert anon.get(f"/{SLUG}/slots?service_id={sid}").status_code == 404


def test_other_business_service_cannot_be_used(app):
    open_shop(app)
    other = setup_owner(app, phone=OTHER_PHONE, e164=OTHER_E164, slug="other-biz")
    other.post("/availability/hours", data=hours_form(range(7)))
    foreign = service_id(app, "other-biz")
    assert app.test_client().get(f"/{SLUG}/slots?service_id={foreign}").status_code == 404
    resp = app.test_client().post("/booking-intents", data=form(app, service_id=str(foreign)))
    assert resp.status_code == 404 and intents(app) == []


def test_hidden_business_cannot_be_booked_even_by_owner(app):
    client = open_shop(app)
    sid = service_id(app)
    with app.app_context():
        b = db.session.scalar(select(Business).where(Business.slug == SLUG))
        b.is_public = False
        db.session.commit()
    assert app.test_client().get(f"/{SLUG}/slots?service_id={sid}").status_code == 404
    assert client.get(f"/{SLUG}/slots?service_id={sid}").status_code == 404
    assert client.post("/booking-intents", data=form(app)).status_code == 404
    assert intents(app) == []


def test_details_page_for_valid_slot_and_redirect_for_invalid(app):
    open_shop(app)
    anon = app.test_client()
    sid = service_id(app)
    ok = anon.get(f"/{SLUG}/book?service_id={sid}&start={ts(10)}")
    assert ok.status_code == 200 and b"10:00 AM" in ok.data and b"M-Pesa phone number" in ok.data
    for bad in (ts(10, 7), ts(8), ts(16, 30), "junk"):  # off-grid, before opening, ends after close, junk
        resp = anon.get(f"/{SLUG}/book?service_id={sid}&start={bad}")
        assert resp.status_code == 302 and "/slots" in resp.headers["Location"]


# --------------------------------------------------------------- holds
def test_create_hold_success(app):
    open_shop(app)
    resp = hold(app)
    assert resp.status_code == 302 and "/booking-status/" in resp.headers["Location"]
    (intent,) = intents(app)
    assert intent.status == "pending_payment"
    assert intent.customer_phone == "+254722111222" and intent.customer_name == "Wanjiku Kamau"
    assert (intent.service_name_snapshot, intent.amount_kes_snapshot, intent.duration_minutes_snapshot) == ("Haircut", 1500, 60)
    assert intent.ends_at - intent.starts_at == timedelta(minutes=60)
    left = (intent.hold_expires_at - utcnow()).total_seconds()
    assert 4 * 60 < left <= 5 * 60
    page = app.test_client().get(resp.headers["Location"]).data.decode()
    assert "Time held for you" in page and "Haircut" in page and "10:00 AM" in page


def test_held_slot_disappears_and_overlaps_are_blocked_adjacent_allowed(app):
    open_shop(app)
    hold(app, start=ts(10))
    html = slot_labels(app)
    for gone in ("9:15 AM", "9:30 AM", "9:45 AM", "10:00 AM", "10:15 AM", "10:30 AM", "10:45 AM"):
        assert f">{gone}</a>" not in html, gone
    for free in ("9:00 AM", "11:00 AM"):  # end exactly at / start exactly at the held interval
        assert f">{free}</a>" in html, free
    # overlap (10:30-11:30 vs held 10:00-11:00) refused
    resp = hold(app, start=ts(10, 30), key="key-bbbbbbbbbb", phone="0733 222 333")
    assert resp.status_code == 302 and "/slots" in resp.headers["Location"]
    assert len(intents(app)) == 1
    # starts exactly when the hold ends: allowed (half-open intervals)
    resp = hold(app, start=ts(11), key="key-cccccccccc", phone="0733 222 333")
    assert "/booking-status/" in resp.headers["Location"] and len(intents(app)) == 2
    # and a booking that ends exactly when the first starts: 9:00-10:00 allowed
    resp = hold(app, start=ts(9), key="key-dddddddddd", phone="0744 333 444")
    assert "/booking-status/" in resp.headers["Location"] and len(intents(app)) == 3


def test_same_idempotency_key_returns_same_hold(app):
    open_shop(app)
    a = hold(app)
    b = hold(app)
    assert a.headers["Location"] == b.headers["Location"] and len(intents(app)) == 1


@pytest.mark.parametrize("override", [
    {"phone": "12345"}, {"phone": ""}, {"name": ""}, {"name": "A"}, {"name": "x" * 81},
    {"idempotency_key": "short"},
])
def test_invalid_details_rejected_and_nothing_held(app, override):
    open_shop(app)
    resp = hold(app, **override)
    assert resp.status_code == 400 and intents(app) == []
    assert b"M-Pesa phone number" in resp.data  # form re-rendered


def test_junk_start_with_bad_details_does_not_crash(app):
    open_shop(app)
    resp = hold(app, start="junk", name="")
    assert resp.status_code in (302, 400) and intents(app) == []


def test_unavailable_time_is_not_held(app):
    open_shop(app)
    for bad in (ts(10, 7), ts(8), ts(18), ts(10, days_ahead=-1), ts(10, days_ahead=90)):
        resp = hold(app, start=bad, key=f"key-{abs(bad)}-xx")
        assert resp.status_code == 302 and "/slots" in resp.headers["Location"]
    assert intents(app) == []


def test_max_open_holds_per_phone(app):
    open_shop(app)
    for i, hour in enumerate((9, 10, 11)):
        assert "/booking-status/" in hold(app, start=ts(hour), key=f"key-phone-{i}-aaa").headers["Location"]
    resp = hold(app, start=ts(14), key="key-phone-9-aaa")
    assert resp.status_code == 400 and len(intents(app)) == 3


def test_expired_hold_frees_the_slot(app):
    open_shop(app)
    hold(app)
    with app.app_context():
        row = db.session.scalar(select(BookingIntent))
        row.hold_expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert "10:00 AM" in slot_labels(app)  # not marked expired yet, but no longer blocking
    resp = hold(app, key="key-eeeeeeeeee", phone="0733 222 333")
    assert "/booking-status/" in resp.headers["Location"]
    with app.app_context():
        assert booking_service.expire_holds() == 1
        assert booking_service.expire_holds() == 0
    assert sorted(i.status for i in intents(app)) == ["expired", "pending_payment"]


def test_status_page_for_expired_hold_offers_rebooking(app):
    open_shop(app)
    loc = hold(app).headers["Location"]
    with app.app_context():
        row = db.session.scalar(select(BookingIntent))
        row.hold_expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    page = app.test_client().get(loc).data.decode()
    assert "hold has ended" in page and "Time held for you" not in page


def test_release_frees_slot_and_is_idempotent(app):
    open_shop(app)
    loc = hold(app).headers["Location"]
    token = loc.rsplit("/", 1)[1]
    client = app.test_client()
    assert client.post(f"/booking-status/{token}/release").status_code == 302
    assert intents(app)[0].status == "expired"
    assert client.post(f"/booking-status/{token}/release").status_code == 302
    assert "/booking-status/" in hold(app, key="key-ffffffffff", phone="0733 222 333").headers["Location"]


def test_paid_booking_blocks_slot_forever(app):
    open_shop(app)
    hold(app)
    with app.app_context():
        row = db.session.scalar(select(BookingIntent))
        row.status = "paid"
        row.hold_expires_at = utcnow() - timedelta(hours=1)
        db.session.commit()
    resp = hold(app, key="key-gggggggggg", phone="0733 222 333")
    assert "/slots" in resp.headers["Location"] and len(intents(app)) == 1


def test_blocked_period_and_break_stop_holds(app):
    client = open_shop(app)
    client.post("/availability/breaks", data=hours_form(range(7), "12:00", "13:00"))
    resp = hold(app, start=ts(11, 30))  # 11:30-12:30 crosses the break
    assert "/slots" in resp.headers["Location"] and intents(app) == []
    client.post("/blocked-periods", data={"start_date": local_day().isoformat(), "all_day": "on"})
    resp = hold(app, start=ts(14), key="key-hhhhhhhhhh")
    assert "/slots" in resp.headers["Location"] and intents(app) == []


def test_snapshot_survives_service_edit(app):
    client = open_shop(app)
    hold(app)
    sid = service_id(app)
    client.post(f"/services/{sid}/edit", data={"name": "Haircut Deluxe", "description": "", "price_kes": "3000",
                                              "duration_minutes": "90", "is_active": "on"})
    row = intents(app)[0]
    assert (row.service_name_snapshot, row.amount_kes_snapshot, row.duration_minutes_snapshot) == ("Haircut", 1500, 60)


def test_status_unknown_token_404_and_json(app):
    open_shop(app)
    anon = app.test_client()
    assert anon.get("/booking-status/nope").status_code == 404
    assert anon.get("/booking-status/nope/json").status_code == 404
    token = hold(app).headers["Location"].rsplit("/", 1)[1]
    data = anon.get(f"/booking-status/{token}/json").get_json()
    assert data["status"] == "pending_payment" and 0 < data["seconds_left"] <= 300


def test_db_rejects_invalid_intent(app):
    open_shop(app)
    hold(app)
    with app.app_context():
        row = db.session.scalar(select(BookingIntent))
        row.status = "bogus"
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        row = db.session.scalar(select(BookingIntent))
        row.ends_at = row.starts_at
        with pytest.raises(IntegrityError):
            db.session.commit()


def test_expire_holds_cli(app):
    open_shop(app)
    hold(app)
    result = app.test_cli_runner().invoke(args=["expire-holds"])
    assert result.exit_code == 0 and "Expired 0 hold(s)." in result.output


# ----------------------------------------------------------- concurrency
def _race(app, starts, count):
    """Fire `count` simultaneous hold requests (round-robin over `starts`). Returns list of results."""
    with app.app_context():
        business_id = db.session.scalar(select(Business.id).where(Business.slug == SLUG))
        sid = db.session.scalar(select(Service.id))
    barrier = threading.Barrier(count)
    results = [None] * count

    def worker(i):
        with app.app_context():
            business = db.session.get(Business, business_id)
            barrier.wait(timeout=10)
            try:
                booking_service.create_hold(
                    business, sid, starts[i % len(starts)], f"Customer {i}", f"07{20000000 + i}",
                    f"race-key-{i:08d}")
                results[i] = "ok"
            except booking_service.SlotUnavailable:
                results[i] = "taken"
            except Exception as exc:  # surface anything unexpected in the assertion
                results[i] = f"error: {exc!r}"
            finally:
                db.session.remove()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    return results


def test_concurrent_requests_for_same_slot_only_one_wins(app):
    open_shop(app)
    results = _race(app, [ts(10)], 8)
    assert results.count("ok") == 1 and results.count("taken") == 7, results
    assert len(intents(app)) == 1


def test_concurrent_overlapping_slots_only_one_wins(app):
    open_shop(app)
    results = _race(app, [ts(10), ts(10, 30), ts(10, 15), ts(10, 45)], 8)  # all overlap each other
    assert results.count("ok") == 1, results
    assert len(intents(app)) == 1


def test_concurrent_non_overlapping_slots_all_succeed(app):
    open_shop(app)
    results = _race(app, [ts(9), ts(10), ts(11), ts(12)], 4)
    assert results == ["ok"] * 4, results
    assert len(intents(app)) == 4
