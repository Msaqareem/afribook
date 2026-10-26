"""AFRIBOOK application factory."""
import logging
import os

from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import get_config
from app.extensions import csrf, db, limiter, login_manager, migrate
from app.integrations.sms import build_sms_provider


def create_app(config_name: str | None = None, overrides: dict | None = None) -> Flask:
    config_name = (config_name or os.environ.get("APP_ENV") or "development").lower()
    config_cls = get_config(config_name)

    app = Flask(__name__)
    app.config.from_object(config_cls)
    if overrides:
        app.config.update(overrides)
    config_cls.validate(app.config)

    if not app.testing:
        logging.basicConfig(
            level=app.config["LOG_LEVEL"],
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )

    hops = app.config["PROXY_FIX_HOPS"]
    if hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    # Extensions
    from app import models  # noqa: F401  (register models with SQLAlchemy metadata)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to continue."
    login_manager.login_message_category = "info"

    app.extensions["sms_provider"] = build_sms_provider(app.config)

    from app.payments import build_payment_provider

    app.extensions["payment_provider"] = build_payment_provider(app.config)

    from app.utils.format import duration, hhmm, kes, local_dt

    app.jinja_env.filters.update(kes=kes, duration=duration, hhmm=hhmm, local_dt=local_dt)

    _register_blueprints(app)
    _register_error_handlers(app)
    _register_security_headers(app)

    from app.cli import register_cli

    register_cli(app)
    return app


def _register_blueprints(app: Flask) -> None:
    from app.blueprints.admin import bp as admin_bp
    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.availability import bp as availability_bp
    from app.blueprints.bookings import bp as bookings_bp
    from app.blueprints.catalog import bp as catalog_bp
    from app.blueprints.health import bp as health_bp
    from app.blueprints.owner import bp as owner_bp
    from app.blueprints.webhooks import bp as webhooks_bp
    from app.blueprints.public import bp as public_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(owner_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(availability_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(bookings_bp)
    app.register_blueprint(webhooks_bp)
    # Public blueprint last: Bucket 2 adds the catch-all /<slug> route to it.
    app.register_blueprint(public_bp)


_ERRORS = {
    400: ("Bad request", "Your request could not be processed. If you were filling in a form, reload the page and try again."),
    403: ("Access denied", "You do not have permission to view this page."),
    404: ("Page not found", "We could not find the page you were looking for."),
    429: ("Too many requests", "You have made too many attempts. Please wait a few minutes and try again."),
    500: ("Something went wrong", "An unexpected error occurred. Please try again later."),
}


def _register_error_handlers(app: Flask) -> None:
    def make_handler(code: int):
        title, message = _ERRORS[code]

        def handler(error):
            if code == 500:
                db.session.rollback()
            return render_template("errors/error.html", code=code, title=title, message=message), code

        return handler

    for code in _ERRORS:
        app.register_error_handler(code, make_handler(code))


def _register_security_headers(app: Flask) -> None:
    @app.after_request
    def add_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response
