"""Fake M-Pesa for development and tests. No money moves. Never allowed in production."""
import hashlib
import hmac
import json
import secrets

from app.payments.base import (EVENT_CANCELLED, EVENT_FAILED, EVENT_PENDING, EVENT_SUCCEEDED, InitiateResult,
                               InvalidWebhook, PaymentEvent, PaymentProvider)

SIGNATURE_HEADER = "X-Mock-Signature"


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class MockPaymentProvider(PaymentProvider):
    name = "mock"

    def __init__(self, secret: str):
        self.secret = secret
        self.initiated: list[dict] = []     # what the app asked us to charge (tests read this)
        self.outcomes: dict[str, PaymentEvent] = {}   # tests may script what query_status returns
        self.fail_next_initiate: str | None = None

    def initiate_stk(self, *, our_reference, phone_e164, amount_kes, narrative):
        if self.fail_next_initiate:
            message, self.fail_next_initiate = self.fail_next_initiate, None
            return InitiateResult(ok=False, message=message)
        reference = "MOCK-" + secrets.token_hex(6).upper()
        self.initiated.append({"our_reference": our_reference, "provider_reference": reference,
                               "phone": phone_e164, "amount_kes": amount_kes, "narrative": narrative})
        return InitiateResult(ok=True, provider_reference=reference, message="Prompt sent (mock).")

    def build_body(self, *, provider_reference, our_reference, status, amount_kes=None, receipt=None,
                   result_code=None, description=None) -> tuple[bytes, dict]:
        """A signed callback exactly as the webhook route will receive it."""
        body = json.dumps({
            "provider_reference": provider_reference, "our_reference": our_reference, "status": status,
            "amount_kes": amount_kes, "receipt": receipt, "result_code": result_code, "description": description,
        }).encode()
        return body, {SIGNATURE_HEADER: sign(self.secret, body), "Content-Type": "application/json"}

    def parse_webhook(self, body, headers):
        given = headers.get(SIGNATURE_HEADER, "")
        if not given or not hmac.compare_digest(given, sign(self.secret, body)):
            raise InvalidWebhook("bad mock signature")
        try:
            data = json.loads(body)
        except ValueError:
            raise InvalidWebhook("unreadable body") from None
        status = data.get("status")
        if status not in (EVENT_SUCCEEDED, EVENT_FAILED, EVENT_CANCELLED, EVENT_PENDING):
            raise InvalidWebhook("unknown status")
        return PaymentEvent(
            status=status, provider_reference=data.get("provider_reference"), our_reference=data.get("our_reference"),
            amount_kes=data.get("amount_kes"), receipt=data.get("receipt"),
            result_code=data.get("result_code"), description=data.get("description"), raw=data,
        )

    def query_status(self, provider_reference, our_reference):
        return self.outcomes.get(provider_reference) or self.outcomes.get(our_reference)
