"""Bucket 6: manual weekly payouts and the payment-review queue (admin only)."""
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.extensions import db
from app.models.booking import Booking, BookingIntent
from app.models.payment import PaymentAttempt, Payout
from app.services import payout_service
from app.services.errors import ValidationError
from app.utils.timeutil import utcnow
from tests.helpers import login, make_user
from tests.integration.test_booking import SLUG, hold, open_shop, ts
from tests.integration.test_business import payment_data
from tests.integration.test_payments import (OTHER_PHONE_LOCAL, attempts, bookings, callback, expire_hold, hold_token,
                                             paid_booking, pay, provider)

ADMIN_E164, ADMIN_LOCAL = "+254700111000", "0700111000"


def admin_client(app):
    make_user(app, phone=ADMIN_E164, role="admin")
    client = app.test_client()
    login(client, phone=ADMIN_LOCAL)
    return client


def set_destination(owner, **kw):
    return owner.post("/settings/payment", data=payment_data(**kw))


def shift_booking_to_past(app, booking_id, days=5):
    """Pretend the appointment happened `days` ago (the exclusion constraint still applies)."""
    with app.app_context():
        b = db.session.get(Booking, booking_id)
        intent_id = b.booking_intent_id
        delta = timedelta(days=days)
        db.session.execute(text("UPDATE bookings SET starts_at = starts_at - :d, ends_at = ends_at - :d WHERE id = :i"),
                           {"d": delta, "i": booking_id})
        db.session.execute(text("UPDATE booking_intents SET starts_at = starts_at - :d, ends_at = ends_at - :d WHERE id = :i"),
                           {"d": delta, "i": intent_id})
        db.session.commit()


def two_finished_bookings(app, owner):
    set_destination(owner)
    paid_booking(app, start=ts(10))
    paid_booking(app, start=ts(12), key="key-second0002", phone=OTHER_PHONE_LOCAL)
    for b in bookings(app):
        shift_booking_to_past(app, b.id)


def payouts(app):
    with app.app_context():
        rows = list(db.session.scalars(select(Payout).order_by(Payout.id)))
        for r in rows:
            db.session.expunge(r)
        return rows


# ------------------------------------------------------------------ access
def test_admin_pages_require_admin(app):
    owner = open_shop(app)
    anon = app.test_client()
    paths = ["/admin/payouts", "/admin/payments/review", "/admin/payouts/1", "/admin/payouts/1/export.csv"]
    for path in paths:
        assert anon.get(path).status_code == 302
        assert owner.get(path).status_code == 403
    for path in ("/admin/payouts/create", "/admin/payouts/1/paid", "/admin/payouts/1/cancel", "/admin/payments/1/resolve"):
        assert anon.post(path).status_code == 302
        assert owner.post(path).status_code == 403


# ------------------------------------------------------------------ owed money
def test_future_appointments_are_not_owed_yet(app):
    owner = open_shop(app)
    set_destination(owner)
    paid_booking(app)
    with app.app_context():
        assert payout_service.owed_summary() == []


def test_owed_is_service_price_only_not_the_fee(app):
    owner = open_shop(app)
    two_finished_bookings(app, owner)
    with app.app_context():
        (row,) = payout_service.owed_summary()
        assert row["count"] == 2 and row["total"] == 3000          # 2 x 1,500, not 2 x 1,523
        assert row["destination"] == "Till 123456"


def test_admin_payout_flow_prepare_send_record(app):
    owner = open_shop(app)
    two_finished_bookings(app, owner)
    admin = admin_client(app)
    page = admin.get("/admin/payouts").data.decode()
    assert "KES 3,000" in page and "Till 123456" in page
    resp = admin.post("/admin/payouts/create", data={"business_id": "1"})
    assert resp.status_code == 302
    (payout,) = payouts(app)
    assert (payout.total_kes, payout.booking_count, payout.status, payout.destination_snapshot) == (3000, 2, "pending", "Till 123456")
    assert all(b.payout_id == payout.id for b in bookings(app))
    # nothing left to prepare: bookings can never be paid twice
    again = admin.post("/admin/payouts/create", data={"business_id": "1"}, follow_redirects=True)
    assert b"Nothing is owed" in again.data and len(payouts(app)) == 1
    detail = admin.get(f"/admin/payouts/{payout.id}").data.decode()
    assert "Send to" in detail and "AB-" in detail
    assert admin.post(f"/admin/payouts/{payout.id}/paid", data={"reference": "x"}).status_code == 302
    assert payouts(app)[0].status == "pending"                   # bad reference refused
    admin.post(f"/admin/payouts/{payout.id}/paid", data={"reference": "sjk9a8b7c6"})
    done = payouts(app)[0]
    assert done.status == "paid" and done.mpesa_reference == "SJK9A8B7C6" and done.paid_at is not None
    again = admin.post(f"/admin/payouts/{payout.id}/paid", data={"reference": "ZZZZZZZZZZ"}, follow_redirects=True)
    assert b"already paid" in again.data and payouts(app)[0].mpesa_reference == "SJK9A8B7C6"
    cancel = admin.post(f"/admin/payouts/{payout.id}/cancel", follow_redirects=True)
    assert b"cannot be cancelled" in cancel.data or b"Only a payout" in cancel.data
    assert payouts(app)[0].status == "paid"


def test_cancel_releases_bookings_for_a_new_payout(app):
    owner = open_shop(app)
    two_finished_bookings(app, owner)
    admin = admin_client(app)
    admin.post("/admin/payouts/create", data={"business_id": "1"})
    payout_id = payouts(app)[0].id
    admin.post(f"/admin/payouts/{payout_id}/cancel")
    assert payouts(app)[0].status == "cancelled" and all(b.payout_id is None for b in bookings(app))
    admin.post("/admin/payouts/create", data={"business_id": "1"})
    assert [p.status for p in payouts(app)] == ["cancelled", "pending"]


def test_only_bookings_finished_by_now_are_included_later_ones_stay_owed(app):
    owner = open_shop(app)
    set_destination(owner)
    paid_booking(app, start=ts(10))
    paid_booking(app, start=ts(12), key="key-second0002", phone=OTHER_PHONE_LOCAL)
    shift_booking_to_past(app, bookings(app)[0].id)              # only the first one has happened
    admin = admin_client(app)
    admin.post("/admin/payouts/create", data={"business_id": "1"})
    assert payouts(app)[0].booking_count == 1 and payouts(app)[0].total_kes == 1500
    assert [b.payout_id is not None for b in bookings(app)] == [True, False]


def test_cannot_prepare_payout_without_destination(app):
    owner = open_shop(app)
    paid_booking(app)
    shift_booking_to_past(app, bookings(app)[0].id)
    admin = admin_client(app)
    page = admin.get("/admin/payouts").data.decode()
    assert "NOT SET" in page
    resp = admin.post("/admin/payouts/create", data={"business_id": "1"}, follow_redirects=True)
    assert b"has not set where to receive payouts" in resp.data and payouts(app) == []


def test_paybill_destination_includes_account(app):
    owner = open_shop(app)
    set_destination(owner, payment_type="PAYBILL", shortcode="400200", account_reference_template="SALON1")
    with app.app_context():
        from app.models.business import Business
        assert payout_service.destination_text(db.session.scalar(select(Business))) == "Paybill 400200 (account SALON1)"


def test_payout_csv_export(app):
    owner = open_shop(app)
    two_finished_bookings(app, owner)
    admin = admin_client(app)
    admin.post("/admin/payouts/create", data={"business_id": "1"})
    resp = admin.get(f"/admin/payouts/{payouts(app)[0].id}/export.csv")
    assert resp.status_code == 200 and resp.mimetype == "text/csv"
    text_ = resp.data.decode()
    assert "booking_reference" in text_ and "TOTAL,,,,3000" in text_ and text_.count("AB-") == 2


def test_unknown_payout_is_404(app):
    admin = admin_client(app)
    assert admin.get("/admin/payouts/999").status_code == 404
    assert admin.post("/admin/payouts/999/paid", data={"reference": "ABCDEF12"}).status_code == 404


def test_database_requires_proof_for_paid_payout(app):
    owner = open_shop(app)
    two_finished_bookings(app, owner)
    admin_client(app).post("/admin/payouts/create", data={"business_id": "1"})
    from sqlalchemy.exc import IntegrityError
    with app.app_context():
        row = db.session.scalar(select(Payout))
        row.status = "paid"
        with pytest.raises(IntegrityError):
            db.session.commit()


# ------------------------------------------------------------- review queue
def test_review_queue_lists_and_resolves(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    callback(app, attempts(app)[0], amount=1500)              # wrong amount -> review
    admin = admin_client(app)
    assert b"1" in admin.get("/admin/").data
    page = admin.get("/admin/payments/review").data.decode()
    assert "Amount mismatch" in page and "KES 1,523" in page and "+254722111222" in page
    attempt_id = attempts(app)[0].id
    assert b"Write a short note" in admin.post(f"/admin/payments/{attempt_id}/resolve", data={"note": "x"}, follow_redirects=True).data
    admin.post(f"/admin/payments/{attempt_id}/resolve", data={"note": "Refunded via M-Pesa ref QWE123RTY"})
    assert attempts(app)[0].resolved_at is not None
    assert b"Nothing to review" in admin.get("/admin/payments/review").data
    # resolving twice does nothing
    assert b"Nothing to resolve" in admin.post(f"/admin/payments/{attempt_id}/resolve", data={"note": "again again"}, follow_redirects=True).data
