"""IntaSend adapter (M-Pesa STK Push collections).

Written from IntaSend's public documentation. It has NOT yet been run against the IntaSend sandbox:
do that (docs/payments.md section 10) before trusting it. Field names marked CHECK are the ones to
confirm first if the sandbox behaves differently.
"""
import hmac
import logging
from decimal import Decimal, InvalidOperation

import requests

from app.payments.base import (EVENT_CANCELLED, EVENT_FAILED, EVENT_PENDING, EVENT_SUCCEEDED, InitiateResult,
                               InvalidWebhook, PaymentEvent, PaymentProvider, ProviderUnavailable)

log = logging.getLogger(__name__)
TIMEOUT = 15
CANCELLED_CODES = {"1032"}   # customer dismissed the prompt (from IntaSend docs)


class IntaSendProvider(PaymentProvider):
    name = "intasend"

    def __init__(self, secret_key: str, test_mode: bool, challenge: str, session=None):
        self.secret_key = secret_key
        self.challenge = challenge
        self.base = "https://sandbox.intasend.com" if test_mode else "https://payment.intasend.com"
        self.http = session or requests

    # ----------------------------------------------------------------- helpers
    def _headers(self):
        return {"Authorization": f"Bearer {self.secret_key}", "Content-Type": "application/json",
                "Accept": "application/json"}

    @staticmethod
    def _amount(value) -> int | None:
        if value in (None, ""):
            return None
        try:
            d = Decimal(str(value))
        except InvalidOperation:
            return -1
        return int(d) if d == d.to_integral_value() else -1   # -1 can never match, so it goes to review

    @staticmethod
    def _status(state: str | None, code) -> str:
        state = (state or "").upper()
        if state == "COMPLETE":
            return EVENT_SUCCEEDED
        if state == "FAILED":
            return EVENT_CANCELLED if str(code) in CANCELLED_CODES else EVENT_FAILED
        return EVENT_PENDING    # PENDING / PROCESSING / anything unknown is not money

    def _event(self, data: dict) -> PaymentEvent:
        code = data.get("failed_code")
        safe = {k: data.get(k) for k in ("invoice_id", "state", "value", "api_ref", "mpesa_reference",
                                         "failed_reason", "failed_code")}   # never keep `challenge`
        return PaymentEvent(
            status=self._status(data.get("state"), code),
            provider_reference=data.get("invoice_id"), our_reference=data.get("api_ref"),
            amount_kes=self._amount(data.get("value")), receipt=data.get("mpesa_reference") or None,
            result_code=str(code) if code else None, description=(data.get("failed_reason") or None), raw=safe,
        )

    # ------------------------------------------------------------------ calls
    def initiate_stk(self, *, our_reference, phone_e164, amount_kes, narrative):
        payload = {"amount": amount_kes, "phone_number": phone_e164.lstrip("+"), "api_ref": our_reference,
                   "narrative": narrative[:100]}   # CHECK: optional fields accepted by your account
        try:
            resp = self.http.post(f"{self.base}/api/v1/payment/mpesa-stk-push/", json=payload,
                                  headers=self._headers(), timeout=TIMEOUT)
        except requests.RequestException:
            log.exception("intasend initiate: network error")
            return InitiateResult(ok=False, message="We could not reach the payment service. Please try again.")
        if resp.status_code >= 400:
            log.warning("intasend initiate failed: HTTP %s", resp.status_code)   # body may hold details: not logged
            return InitiateResult(ok=False, message="The M-Pesa prompt could not be sent. Check the number and try again.")
        try:
            invoice = resp.json().get("invoice") or {}
            reference = invoice.get("invoice_id")
        except ValueError:
            reference = None
        if not reference:
            return InitiateResult(ok=False, message="The payment service gave an unexpected answer. Please try again.")
        return InitiateResult(ok=True, provider_reference=reference, message="Check your phone for the M-Pesa prompt.")

    def parse_webhook(self, body, headers):
        import json
        try:
            data = json.loads(body)
            assert isinstance(data, dict)
        except (ValueError, AssertionError):
            raise InvalidWebhook("unreadable body") from None
        sent = str(data.get("challenge") or "")
        if not self.challenge or not hmac.compare_digest(sent, self.challenge):
            raise InvalidWebhook("challenge mismatch")
        return self._event(data)

    def query_status(self, provider_reference, our_reference):
        if not provider_reference:
            return None
        try:
            resp = self.http.post(f"{self.base}/api/v1/payment/status/", json={"invoice_id": provider_reference},
                                  headers=self._headers(), timeout=TIMEOUT)
        except requests.RequestException:
            raise ProviderUnavailable("network") from None
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            return None
        try:
            invoice = resp.json().get("invoice") or {}
        except ValueError:
            raise ProviderUnavailable("bad json") from None
        invoice.setdefault("invoice_id", provider_reference)
        return self._event(invoice)

    def verify_event(self, event):
        """Never trust a webhook alone: money is accepted only if IntaSend's own API says COMPLETE
        for the same invoice and the same amount."""
        if event.status != EVENT_SUCCEEDED:
            return event
        confirmed = self.query_status(event.provider_reference, event.our_reference)
        if confirmed is None or confirmed.status != EVENT_SUCCEEDED:
            raise ProviderUnavailable("could not confirm payment with provider")
        confirmed.our_reference = confirmed.our_reference or event.our_reference
        return confirmed
