"""Bucket 6: paying for a hold, callbacks, confirmed bookings, late/odd payments, reconciliation."""
import threading
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.booking import Booking, BookingIntent
from app.models.payment import PaymentAttempt
from app.services import payment_service
from app.utils.timeutil import utcnow
from tests.helpers import login, make_user
from tests.integration.test_booking import SLUG, form, hold, intents, open_shop, slot_labels, ts

OTHER_PHONE_LOCAL = "0733 222 333"


# ------------------------------------------------------------------ helpers
def attempts(app):
    with app.app_context():
        rows = list(db.session.scalars(select(PaymentAttempt).order_by(PaymentAttempt.id)))
        for r in rows:
            db.session.expunge(r)
        return rows


def bookings(app):
    with app.app_context():
        rows = list(db.session.scalars(select(Booking).order_by(Booking.id)))
        for r in rows:
            db.session.expunge(r)
        return rows


def provider(app):
    return app.extensions["payment_provider"]


def token_of(response):
    return response.headers["Location"].rsplit("/", 1)[1]


def hold_token(app, **kw):
    return token_of(hold(app, **kw))


def pay(app, token, client=None):
    return (client or app.test_client()).post(f"/booking-status/{token}/pay")


def callback(app, attempt, status="succeeded", amount="auto", receipt="RCPT0001", client=None, **extra):
    body, headers = provider(app).build_body(
        provider_reference=attempt.provider_reference, our_reference=attempt.our_reference, status=status,
        amount_kes=attempt.amount_kes if amount == "auto" else amount, receipt=receipt, **extra)
    return (client or app.test_client()).post("/webhooks/payments/mock", data=body, headers=headers)


def paid_booking(app, token=None, **kw):
    token = token or hold_token(app, **kw)
    pay(app, token)
    attempt = attempts(app)[-1]
    assert callback(app, attempt, receipt=f"R{len(attempts(app)):04d}XYZ").get_json()["outcome"] == "confirmed"
    return token


def expire_hold(app, minutes_ago=1):
    with app.app_context():
        for row in db.session.scalars(select(BookingIntent)):
            row.hold_expires_at = utcnow() - timedelta(minutes=minutes_ago)
        db.session.commit()


# ------------------------------------------------------------------- fee shown
def test_fee_is_separate_visible_and_business_keeps_full_price(app):
    open_shop(app)
    details = app.test_client().get(f"/{SLUG}/book?service_id={form(app)['service_id']}&start={ts()}").data.decode()
    assert "Payment fee (1.5%" in details and "KES 23" in details and "KES 1,523" in details
    assert "KES 1,500" in details
    token = hold_token(app)
    (intent,) = intents(app)
    assert (intent.amount_kes_snapshot, intent.fee_kes_snapshot, intent.fee_bps_snapshot) == (1500, 23, 150)
    page = app.test_client().get(f"/booking-status/{token}").data.decode()
    assert "Service price" in page and "Payment fee" in page and "KES 1,523" in page
    assert "payment fee" in slot_labels(app).lower()


def test_fee_snapshot_does_not_change_when_rate_changes(app):
    open_shop(app)
    token = hold_token(app)
    app.config["PAYMENT_FEE_BPS"] = 500
    pay(app, token)
    assert attempts(app)[0].amount_kes == 1523          # charged what the customer was shown


def test_zero_fee_rate(app):
    open_shop(app)
    app.config["PAYMENT_FEE_BPS"] = 0
    hold(app)
    assert intents(app)[0].fee_kes_snapshot == 0


# ------------------------------------------------------------------- starting
def test_pay_sends_prompt_for_total_to_customers_phone(app):
    open_shop(app)
    token = hold_token(app)
    resp = pay(app, token)
    assert resp.status_code == 302
    (attempt,) = attempts(app)
    assert attempt.status == "pending" and attempt.amount_kes == 1523 and attempt.customer_phone == "+254722111222"
    assert attempt.provider_reference.startswith("MOCK-")
    sent = provider(app).initiated
    assert len(sent) == 1 and sent[0]["amount_kes"] == 1523 and sent[0]["phone"] == "+254722111222"
    page = app.test_client().get(f"/booking-status/{token}").data.decode()
    assert "Check your phone" in page and "Test mode" in page


def test_hold_is_stretched_to_the_grace_window(app):
    open_shop(app)
    token = hold_token(app)
    with app.app_context():
        row = db.session.scalar(select(BookingIntent))
        row.hold_expires_at = utcnow() + timedelta(seconds=30)
        db.session.commit()
    pay(app, token)
    left = (intents(app)[0].hold_expires_at - utcnow()).total_seconds()
    assert left > 2 * 60


def test_double_click_creates_one_attempt_one_prompt(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    pay(app, token)
    assert len(attempts(app)) == 1 and len(provider(app).initiated) == 1


def test_cannot_pay_for_expired_hold(app):
    open_shop(app)
    token = hold_token(app)
    expire_hold(app)
    resp = pay(app, token)
    assert resp.status_code == 302 and attempts(app) == []
    assert b"hold has ended" in app.test_client().get(f"/booking-status/{token}").data


def test_unknown_token_cannot_pay(app):
    open_shop(app)
    assert app.test_client().post("/booking-status/nope/pay").status_code == 404


def test_prompt_failure_keeps_hold_and_allows_retry(app):
    open_shop(app)
    token = hold_token(app)
    provider(app).fail_next_initiate = "The M-Pesa prompt could not be sent. Check the number and try again."
    pay(app, token)
    assert attempts(app)[0].status == "failed" and intents(app)[0].status == "pending_payment"
    page = app.test_client().get(f"/booking-status/{token}").data.decode()
    assert "did not go through" in page and "Pay KES 1,523" in page
    pay(app, token)
    assert [a.status for a in attempts(app)] == ["failed", "pending"]


def test_attempt_limit_per_hold(app):
    open_shop(app)
    token = hold_token(app)
    for _ in range(3):
        provider(app).fail_next_initiate = "nope"
        pay(app, token)
    pay(app, token)
    assert len(attempts(app)) == 3 and len(provider(app).initiated) == 0


def test_cannot_release_while_payment_in_progress(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    app.test_client().post(f"/booking-status/{token}/release")
    assert intents(app)[0].status == "pending_payment"


# ------------------------------------------------------------------ success
def test_verified_success_creates_booking_and_blocks_slot(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    attempt = attempts(app)[0]
    resp = callback(app, attempt)
    assert resp.status_code == 200 and resp.get_json() == {"ok": True, "outcome": "confirmed"}
    (booking,) = bookings(app)
    assert booking.public_reference.startswith("AB-") and booking.status == "confirmed"
    assert (booking.amount_kes_snapshot, booking.fee_kes_snapshot) == (1500, 23)
    assert booking.customer_phone_snapshot == "+254722111222"
    assert intents(app)[0].status == "paid"
    done = attempts(app)[0]
    assert done.status == "succeeded" and done.mpesa_receipt == "RCPT0001" and done.completed_at is not None
    page = app.test_client().get(f"/booking-status/{token}").data.decode()
    assert booking.public_reference in page and "Payment received" in page and "RCPT0001" in page
    assert ">10:00 AM</a>" not in slot_labels(app)
    assert "/slots" in hold(app, key="key-zzzzzzzzzz", phone=OTHER_PHONE_LOCAL).headers["Location"]


def test_duplicate_callback_changes_nothing(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    attempt = attempts(app)[0]
    assert callback(app, attempt).get_json()["outcome"] == "confirmed"
    assert callback(app, attempt).get_json()["outcome"] == "duplicate"
    assert callback(app, attempt, status="failed").get_json()["outcome"] == "duplicate"   # late contradictory noise
    assert len(bookings(app)) == 1 and attempts(app)[0].status == "succeeded"


def test_success_after_hold_expired_but_slot_still_free_is_confirmed(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    attempt = attempts(app)[0]
    expire_hold(app)
    assert callback(app, attempt).get_json()["outcome"] == "confirmed"
    assert len(bookings(app)) == 1


def test_success_after_slot_was_rebooked_goes_to_review_without_double_booking(app):
    open_shop(app)
    first = hold_token(app)
    pay(app, first)
    late = attempts(app)[0]
    expire_hold(app)
    second = hold_token(app, key="key-secondhold1", phone=OTHER_PHONE_LOCAL)   # same slot, now free again
    pay(app, second)
    paid_second = attempts(app)[-1]
    assert callback(app, paid_second, receipt="RCPTSECOND").get_json()["outcome"] == "confirmed"
    assert callback(app, late, receipt="RCPTLATE01").get_json()["outcome"] == "review"
    assert len(bookings(app)) == 1                      # never two bookings for one slot
    states = {a.id: a.status for a in attempts(app)}
    assert states[late.id] == "review"
    page = app.test_client().get(f"/booking-status/{first}").data.decode()
    assert "could not confirm this booking automatically" in page and "RCPTLATE01" in page


def test_wrong_amount_goes_to_review(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    attempt = attempts(app)[0]
    assert callback(app, attempt, amount=1500).get_json()["outcome"] == "review"
    assert bookings(app) == [] and intents(app)[0].status == "review"
    assert ">10:00 AM</a>" in slot_labels(app)         # slot is free again for others


def test_missing_amount_goes_to_review(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    assert callback(app, attempts(app)[0], amount=None).get_json()["outcome"] == "review"
    assert bookings(app) == []


def test_cancelled_then_retry_then_success(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    assert callback(app, attempts(app)[0], status="cancelled", amount=None, result_code="1032").get_json()["outcome"] == "cancelled"
    assert intents(app)[0].status == "pending_payment" and bookings(app) == []
    page = app.test_client().get(f"/booking-status/{token}").data.decode()
    assert "You cancelled the M-Pesa prompt" in page and "Pay KES 1,523" in page
    pay(app, token)
    assert callback(app, attempts(app)[1]).get_json()["outcome"] == "confirmed"
    assert [a.status for a in attempts(app)] == ["cancelled", "succeeded"]


def test_failed_payment_keeps_hold(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    callback(app, attempts(app)[0], status="failed", amount=None, result_code="1", description="Insufficient balance")
    assert attempts(app)[0].status == "failed" and attempts(app)[0].result_description == "Insufficient balance"
    assert bookings(app) == []


def test_pending_event_changes_nothing(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    assert callback(app, attempts(app)[0], status="pending", amount=None).get_json()["outcome"] == "pending"
    assert attempts(app)[0].status == "pending"


# ------------------------------------------------------------- webhook security
def test_webhook_rejects_bad_or_missing_signature(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    attempt = attempts(app)[0]
    body, headers = provider(app).build_body(
        provider_reference=attempt.provider_reference, our_reference=attempt.our_reference,
        status="succeeded", amount_kes=attempt.amount_kes, receipt="FORGED0001")
    client = app.test_client()
    assert client.post("/webhooks/payments/mock", data=body).status_code == 400
    assert client.post("/webhooks/payments/mock", data=body, headers={"X-Mock-Signature": "0" * 64}).status_code == 400
    assert client.post("/webhooks/payments/mock", data=body + b" ", headers=headers).status_code == 400   # body tampered
    assert client.post("/webhooks/payments/mock", data=b"not json", headers=headers).status_code == 400
    assert bookings(app) == [] and attempts(app)[0].status == "pending"


def test_webhook_for_inactive_provider_is_rejected(app):
    assert app.test_client().post("/webhooks/payments/intasend", data=b"{}").status_code == 400


def test_webhook_unknown_reference_is_ignored(app):
    open_shop(app)
    body, headers = provider(app).build_body(provider_reference="MOCK-NOPE", our_reference="ABNOPE", status="succeeded",
                                             amount_kes=100, receipt="X")
    resp = app.test_client().post("/webhooks/payments/mock", data=body, headers=headers)
    assert resp.status_code == 200 and resp.get_json()["outcome"] == "ignored" and bookings(app) == []


def test_webhook_is_post_only(app):
    assert app.test_client().get("/webhooks/payments/mock").status_code == 405


def test_browser_cannot_create_a_booking(app):
    open_shop(app)
    token = hold_token(app)
    client = app.test_client()
    pay(app, token, client)
    for path in (f"/booking-status/{token}", f"/booking-status/{token}/json"):
        client.get(path)
    client.post(f"/booking-status/{token}/pay")
    assert bookings(app) == [] and intents(app)[0].status == "pending_payment"


# ------------------------------------------------------- mock phone (dev page)
def test_mock_phone_buttons_drive_the_real_callback_path(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    ref = attempts(app)[0].provider_reference
    client = app.test_client()
    assert client.post(f"/dev/mock-pay/{ref}/paid_wrong_amount").status_code == 302
    assert bookings(app) == [] and attempts(app)[0].status == "review"
    token2 = hold_token(app, key="key-another0001", phone=OTHER_PHONE_LOCAL)
    pay(app, token2)
    ref2 = attempts(app)[-1].provider_reference
    client.post(f"/dev/mock-pay/{ref2}/paid_twice")
    assert len(bookings(app)) == 1


def test_mock_page_disabled_when_not_allowed(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    ref = attempts(app)[0].provider_reference
    app.config["ALLOW_MOCK_PAYMENT_PAGE"] = False
    assert app.test_client().post(f"/dev/mock-pay/{ref}/paid").status_code == 404
    assert bookings(app) == []
    assert b"Test mode" not in app.test_client().get(f"/booking-status/{token}").data


def test_mock_page_rejects_unknown_action(app):
    open_shop(app)
    pay(app, hold_token(app))
    ref = attempts(app)[0].provider_reference
    assert app.test_client().post(f"/dev/mock-pay/{ref}/hack").status_code == 404


# ----------------------------------------------------------------- reconciliation
def age_attempt(app, minutes):
    with app.app_context():
        row = db.session.scalar(select(PaymentAttempt))
        row.initiated_at = utcnow() - timedelta(minutes=minutes)
        db.session.commit()


def test_reconcile_confirms_payment_whose_callback_never_came(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    attempt = attempts(app)[0]
    age_attempt(app, 5)
    from app.payments.base import EVENT_SUCCEEDED, PaymentEvent
    provider(app).outcomes[attempt.provider_reference] = PaymentEvent(
        status=EVENT_SUCCEEDED, provider_reference=attempt.provider_reference, our_reference=attempt.our_reference,
        amount_kes=attempt.amount_kes, receipt="RECON00001")
    with app.app_context():
        result = payment_service.reconcile_pending()
    assert result["checked"] == 1 and result["resolved"] == 1
    assert len(bookings(app)) == 1 and attempts(app)[0].mpesa_receipt == "RECON00001"


def test_status_polling_triggers_reconciliation(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    attempt = attempts(app)[0]
    age_attempt(app, 5)
    from app.payments.base import EVENT_SUCCEEDED, PaymentEvent
    provider(app).outcomes[attempt.provider_reference] = PaymentEvent(
        status=EVENT_SUCCEEDED, provider_reference=attempt.provider_reference, our_reference=attempt.our_reference,
        amount_kes=attempt.amount_kes, receipt="POLL000001")
    data = app.test_client().get(f"/booking-status/{token}/json").get_json()
    assert data["state"] == "paid" and len(bookings(app)) == 1


def test_recent_attempts_are_not_reconciled_yet(app):
    open_shop(app)
    pay(app, hold_token(app))
    with app.app_context():
        assert payment_service.reconcile_pending()["checked"] == 0


def test_unanswered_attempts_time_out_after_fifteen_minutes(app):
    open_shop(app)
    pay(app, hold_token(app))
    age_attempt(app, 20)
    with app.app_context():
        result = payment_service.reconcile_pending()
    assert result["timed_out"] == 1 and attempts(app)[0].status == "timeout"


def test_late_success_on_a_timed_out_attempt_is_still_honoured_if_slot_free(app):
    open_shop(app)
    token = hold_token(app)
    pay(app, token)
    age_attempt(app, 20)
    with app.app_context():
        payment_service.reconcile_pending()
    expire_hold(app)
    assert callback(app, attempts(app)[0]).get_json()["outcome"] == "confirmed"


def test_reconcile_cli(app):
    result = app.test_cli_runner().invoke(args=["reconcile-payments"])
    assert result.exit_code == 0 and "checked 0" in result.output


# ----------------------------------------------------------------- DB safety nets
def test_database_refuses_overlapping_confirmed_bookings(app):
    open_shop(app)
    paid_booking(app, start=ts(10))                       # 10:00-11:00
    paid_booking(app, start=ts(11), key="key-second0001", phone=OTHER_PHONE_LOCAL)   # 11:00-12:00 (adjacent: fine)
    assert len(bookings(app)) == 2
    with app.app_context():
        second = db.session.scalars(select(Booking).order_by(Booking.id)).all()[1]
        second.starts_at = second.starts_at - timedelta(minutes=30)     # now overlaps the first
        second.ends_at = second.ends_at - timedelta(minutes=30)
        with pytest.raises(IntegrityError):
            db.session.commit()


def test_one_booking_per_payment_and_per_hold(app):
    open_shop(app)
    paid_booking(app)
    with app.app_context():
        existing = db.session.scalar(select(Booking))
        clone = Booking(
            public_reference="AB-CLONE1", booking_intent_id=existing.booking_intent_id,
            payment_attempt_id=existing.payment_attempt_id, business_id=existing.business_id,
            service_id=existing.service_id, customer_name_snapshot="X", customer_phone_snapshot="+254700000000",
            service_name_snapshot="Haircut", duration_minutes_snapshot=60, amount_kes_snapshot=1500,
            fee_kes_snapshot=23, starts_at=existing.starts_at + timedelta(days=1),
            ends_at=existing.ends_at + timedelta(days=1), status="confirmed")
        db.session.add(clone)
        with pytest.raises(IntegrityError):
            db.session.commit()


def test_only_one_live_prompt_per_hold_at_database_level(app):
    open_shop(app)
    pay(app, hold_token(app))
    with app.app_context():
        existing = db.session.scalar(select(PaymentAttempt))
        db.session.add(PaymentAttempt(
            booking_intent_id=existing.booking_intent_id, provider="mock", our_reference="ABDUPLICATE01",
            amount_kes=1523, customer_phone="+254722111222", status="pending"))
        with pytest.raises(IntegrityError):
            db.session.commit()


# ----------------------------------------------------------------- concurrency
def test_simultaneous_duplicate_callbacks_create_exactly_one_booking(app):
    open_shop(app)
    pay(app, hold_token(app))
    attempt = attempts(app)[0]
    body, headers = provider(app).build_body(
        provider_reference=attempt.provider_reference, our_reference=attempt.our_reference,
        status="succeeded", amount_kes=attempt.amount_kes, receipt="RACE000001")
    barrier = threading.Barrier(6)
    outcomes = [None] * 6

    def worker(i):
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                outcomes[i] = payment_service.handle_webhook("mock", body, headers)
            except Exception as exc:
                outcomes[i] = f"error: {exc!r}"
            finally:
                db.session.remove()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert outcomes.count("confirmed") == 1 and outcomes.count("duplicate") == 5, outcomes
    assert len(bookings(app)) == 1


# ------------------------------------------------------------ production guard
def test_production_config_refuses_mock_payments_and_missing_keys():
    from app.config import ProductionConfig
    base = {"SECRET_KEY": "x" * 40, "SQLALCHEMY_DATABASE_URI": "postgresql://u@h/d", "SMS_PROVIDER": "africastalking"}
    with pytest.raises(RuntimeError):
        ProductionConfig.validate({**base, "PAYMENT_PROVIDER": "mock"})
    with pytest.raises(RuntimeError):
        ProductionConfig.validate({**base, "PAYMENT_PROVIDER": "intasend"})
    ProductionConfig.validate({**base, "PAYMENT_PROVIDER": "intasend", "INTASEND_SECRET_KEY": "k",
                               "INTASEND_WEBHOOK_CHALLENGE": "c"})
