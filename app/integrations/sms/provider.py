from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SmsResult:
    ok: bool
    provider_message_id: str | None = None
    error: str | None = None


class SmsProvider(ABC):
    """Interface every SMS adapter implements (mock now, Africa's Talking in Bucket 7)."""

    @abstractmethod
    def send(self, to_e164: str, message: str) -> SmsResult:
        ...
