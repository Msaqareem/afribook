"""Payment provider contract. Booking code talks to this, never to a specific provider."""
from dataclasses import dataclass, field

EVENT_SUCCEEDED = "succeeded"
EVENT_FAILED = "failed"
EVENT_CANCELLED = "cancelled"
EVENT_PENDING = "pending"


class InvalidWebhook(Exception):
    """Signature/challenge missing or wrong, or body unreadable. Never act on such a message."""


class ProviderUnavailable(Exception):
    """The provider could not be reached or answered with an error. Safe to retry later."""


@dataclass
class InitiateResult:
    ok: bool
    provider_reference: str | None = None
    message: str = ""          # safe to show to the customer


@dataclass
class PaymentEvent:
    status: str                              # EVENT_*
    provider_reference: str | None = None
    our_reference: str | None = None
    amount_kes: int | None = None            # None = provider did not tell us
    receipt: str | None = None               # M-Pesa receipt, e.g. "SJK1A2B3C4"
    result_code: str | None = None
    description: str | None = None
    raw: dict = field(default_factory=dict)  # sanitized copy of what the provider sent (no secrets)


class PaymentProvider:
    name = "base"

    def initiate_stk(self, *, our_reference: str, phone_e164: str, amount_kes: int, narrative: str) -> InitiateResult:
        """Ask the provider to send the M-Pesa prompt to the customer's phone."""
        raise NotImplementedError

    def parse_webhook(self, body: bytes, headers) -> PaymentEvent:
        """Authenticate and decode a callback. Raise InvalidWebhook if it cannot be trusted."""
        raise NotImplementedError

    def verify_event(self, event: PaymentEvent) -> PaymentEvent:
        """Second check before money is accepted (e.g. ask the provider's API). Default: trust parse_webhook."""
        return event

    def query_status(self, provider_reference: str | None, our_reference: str) -> PaymentEvent | None:
        """Ask the provider what happened (for missing callbacks). None = unknown."""
        return None
