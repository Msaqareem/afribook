import logging

from flask import Blueprint, jsonify, request

from app.extensions import csrf
from app.payments.base import InvalidWebhook, ProviderUnavailable
from app.services import payment_service

log = logging.getLogger(__name__)
bp = Blueprint("webhooks", __name__, url_prefix="/webhooks")


@bp.post("/payments/<provider_name>")
@csrf.exempt   # called by the payment provider, not a browser. Authenticity is checked inside the provider adapter.
def payments(provider_name):
    try:
        outcome = payment_service.handle_webhook(provider_name, request.get_data(), request.headers)
    except InvalidWebhook:
        log.warning("rejected %s webhook (failed authenticity check)", provider_name)
        return jsonify(ok=False, error="invalid"), 400
    except ProviderUnavailable:
        return jsonify(ok=False, error="try again later"), 503   # provider will retry; reconcile job is the backstop
    return jsonify(ok=True, outcome=outcome), 200
