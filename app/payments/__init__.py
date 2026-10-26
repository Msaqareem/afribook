from app.payments.base import InvalidWebhook, PaymentEvent, PaymentProvider, ProviderUnavailable
from app.payments.intasend import IntaSendProvider
from app.payments.mock import MockPaymentProvider


def build_payment_provider(config) -> PaymentProvider:
    name = (config.get("PAYMENT_PROVIDER") or "mock").lower()
    if name == "mock":
        return MockPaymentProvider(config["MOCK_PAYMENT_SECRET"])
    if name == "intasend":
        return IntaSendProvider(config["INTASEND_SECRET_KEY"], config["INTASEND_TEST_MODE"],
                                config["INTASEND_WEBHOOK_CHALLENGE"])
    raise RuntimeError(f"Unknown PAYMENT_PROVIDER '{name}'. Use mock or intasend.")


__all__ = ["build_payment_provider", "InvalidWebhook", "PaymentEvent", "PaymentProvider", "ProviderUnavailable"]
