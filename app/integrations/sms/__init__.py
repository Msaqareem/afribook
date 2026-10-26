from flask import current_app

from app.integrations.sms.mock_adapter import MockSmsProvider
from app.integrations.sms.provider import SmsProvider, SmsResult

__all__ = ["MockSmsProvider", "SmsProvider", "SmsResult", "build_sms_provider", "get_sms_provider"]


def build_sms_provider(config) -> SmsProvider:
    name = (config.get("SMS_PROVIDER") or "mock").lower()
    if name == "mock":
        return MockSmsProvider(echo=bool(config.get("MOCK_SMS_ECHO")))
    raise RuntimeError(
        f"Unsupported SMS_PROVIDER '{name}'. The Africa's Talking adapter is built in Bucket 7."
    )


def get_sms_provider() -> SmsProvider:
    return current_app.extensions["sms_provider"]
