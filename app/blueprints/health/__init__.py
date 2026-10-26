import logging

from flask import Blueprint, jsonify
from sqlalchemy import text

from app.extensions import db

bp = Blueprint("health", __name__)
logger = logging.getLogger(__name__)


@bp.get("/health")
def health():
    """Deployment health check. Reveals nothing beyond ok/error."""
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health_db_check_failed")
        db.session.rollback()
        return jsonify(status="error"), 503
    return jsonify(status="ok"), 200
