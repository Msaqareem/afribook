import json

import pytest
import requests

from app.payments.base import (EVENT_CANCELLED, EVENT_FAILED, EVENT_PENDING, EVENT_SUCCEEDED, InvalidWebhook,
                               ProviderUnavailable)
from app.payments.intasend import IntaSendProvider
from app.payments.mock import MockPaymentProvider


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class FakeHttp:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.error:
            raise self.error
        return self.response


def provider(response=None, error=None, test_mode=True, challenge="chal-123"):
    http = FakeHttp(response, error)
    return IntaSendProvider("SECRET-KEY", test_mode, challenge, session=http), http


def webhook(prov, **fields):
    data = {"invoice_id": "INV1", "state": "COMPLETE", "value": "1523.00", "api_ref": "ABREF", "mpesa_reference": "SJK1",
            "failed_reason": None, "failed_code": None, "challenge": "chal-123"}
    data.update(fields)
    return prov.parse_webhook(json.dumps(data).encode(), {})


# ------------------------------------------------------------------- IntaSend
def test_initiate_sends_expected_request_and_returns_invoice_id():
    prov, http = provider(FakeResponse(200, {"invoice": {"invoice_id": "INV9", "state": "PENDING"}}))
    result = prov.initiate_stk(our_reference="ABREF1", phone_e164="+254722111222", amount_kes=1523, narrative="Haircut booking")
    assert result.ok and result.provider_reference == "INV9"
    call = http.calls[0]
    assert call["url"] == "https://sandbox.intasend.com/api/v1/payment/mpesa-stk-push/"
    assert call["headers"]["Authorization"] == "Bearer SECRET-KEY"
    assert call["json"]["phone_number"] == "254722111222" and call["json"]["amount"] == 1523
    assert call["json"]["api_ref"] == "ABREF1" and call["timeout"] == 15


def test_live_mode_uses_production_host():
    prov, http = provider(FakeResponse(200, {"invoice": {"invoice_id": "I"}}), test_mode=False)
    prov.initiate_stk(our_reference="A", phone_e164="+254722111222", amount_kes=10, narrative="x")
    assert http.calls[0]["url"].startswith("https://payment.intasend.com/")


@pytest.mark.parametrize("response,error", [
    (FakeResponse(400, {"detail": "secret internal detail"}), None),
    (FakeResponse(500, None), None),
    (FakeResponse(200, {"unexpected": True}), None),
    (None, requests.ConnectionError("down")),
])
def test_initiate_failures_are_safe_and_generic(response, error):
    prov, _ = provider(response, error)
    result = prov.initiate_stk(our_reference="A", phone_e164="+254722111222", amount_kes=10, narrative="x")
    assert not result.ok and result.provider_reference is None
    assert "secret" not in result.message and result.message


def test_webhook_requires_matching_challenge():
    prov, _ = provider()
    assert webhook(prov).status == EVENT_SUCCEEDED
    for bad in ("wrong", "", None):
        with pytest.raises(InvalidWebhook):
            webhook(prov, challenge=bad)
    with pytest.raises(InvalidWebhook):
        prov.parse_webhook(b"not json", {})
    with pytest.raises(InvalidWebhook):
        prov.parse_webhook(b"[1,2]", {})


def test_webhook_refused_when_no_challenge_configured():
    prov, _ = provider(challenge="")
    with pytest.raises(InvalidWebhook):
        webhook(prov, challenge="")


def test_event_mapping():
    prov, _ = provider()
    ok = webhook(prov)
    assert (ok.status, ok.amount_kes, ok.receipt, ok.provider_reference, ok.our_reference) == (EVENT_SUCCEEDED, 1523, "SJK1", "INV1", "ABREF")
    assert webhook(prov, state="FAILED", failed_code="1032", value=None).status == EVENT_CANCELLED
    assert webhook(prov, state="FAILED", failed_code="1", failed_reason="Insufficient").status == EVENT_FAILED
    for state in ("PENDING", "PROCESSING", "SOMETHING_NEW"):
        assert webhook(prov, state=state).status == EVENT_PENDING          # unknown never counts as money


def test_fractional_or_garbage_amount_can_never_match():
    prov, _ = provider()
    assert webhook(prov, value="1523.50").amount_kes == -1
    assert webhook(prov, value="abc").amount_kes == -1
    assert webhook(prov, value=None).amount_kes is None


def test_challenge_is_never_kept_in_stored_payload():
    prov, _ = provider()
    assert "challenge" not in webhook(prov).raw


def test_verify_event_trusts_the_providers_api_not_the_webhook():
    prov, http = provider(FakeResponse(200, {"invoice": {"invoice_id": "INV1", "state": "COMPLETE", "value": "1523.00",
                                                          "mpesa_reference": "REAL1", "api_ref": "ABREF"}}))
    event = webhook(prov, mpesa_reference="FORGED", value="9999.00")      # webhook lies about amount and receipt
    confirmed = prov.verify_event(event)
    assert confirmed.amount_kes == 1523 and confirmed.receipt == "REAL1"   # API answer wins
    assert http.calls[0]["url"].endswith("/api/v1/payment/status/") and http.calls[0]["json"] == {"invoice_id": "INV1"}


def test_verify_event_refuses_when_api_does_not_confirm():
    for reply in (FakeResponse(200, {"invoice": {"state": "PENDING"}}), FakeResponse(404, {})):
        prov, _ = provider(reply)
        with pytest.raises(ProviderUnavailable):
            prov.verify_event(webhook(prov))
    prov, _ = provider(error=requests.Timeout())
    with pytest.raises(ProviderUnavailable):
        prov.verify_event(webhook(prov))
    prov, _ = provider(FakeResponse(503, {}))
    with pytest.raises(ProviderUnavailable):
        prov.verify_event(webhook(prov))


def test_verify_event_skips_non_success_events():
    prov, http = provider()
    event = webhook(prov, state="FAILED", failed_code="1")
    assert prov.verify_event(event) is event and http.calls == []


def test_query_status_without_reference_is_none():
    prov, http = provider()
    assert prov.query_status(None, "ABREF") is None and http.calls == []


# ---------------------------------------------------------------------- mock
def test_mock_signature_roundtrip_and_tamper_detection():
    prov = MockPaymentProvider("s3cret")
    body, headers = prov.build_body(provider_reference="MOCK-1", our_reference="AB1", status="succeeded", amount_kes=100, receipt="R1")
    event = prov.parse_webhook(body, headers)
    assert event.status == "succeeded" and event.amount_kes == 100
    with pytest.raises(InvalidWebhook):
        prov.parse_webhook(body.replace(b"100", b"101"), headers)
    with pytest.raises(InvalidWebhook):
        prov.parse_webhook(body, {})
    other = MockPaymentProvider("different")
    with pytest.raises(InvalidWebhook):
        other.parse_webhook(body, headers)


def test_mock_initiate_records_request():
    prov = MockPaymentProvider("s")
    result = prov.initiate_stk(our_reference="AB1", phone_e164="+254722111222", amount_kes=50, narrative="n")
    assert result.ok and prov.initiated[0]["amount_kes"] == 50
