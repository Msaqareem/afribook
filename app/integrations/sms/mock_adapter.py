import logging

from app.integrations.sms.provider import SmsProvider, SmsResult
from app.utils.phone import mask_phone

logger = logging.getLogger(__name__)


class MockSmsProvider(SmsProvider):
    """Records messages in memory instead of sending them (development and tests)."""

    def __init__(self, echo: bool = False):
        self.echo = echo
        self.outbox: list[tuple[str, str]] = []
        self.fail_next = False

    def send(self, to_e164: str, message: str) -> SmsResult:
        if self.fail_next:
            self.fail_next = False
            return SmsResult(ok=False, error="mock failure")
        self.outbox.append((to_e164, message))
        if self.echo:
            # DEV ONLY: lets a developer read the OTP from the console.
            logger.info("MOCK SMS to %s: %s", to_e164, message)
        else:
            logger.info("mock sms to %s (%d chars)", mask_phone(to_e164), len(message))
        return SmsResult(ok=True, provider_message_id=f"mock-{len(self.outbox)}")
