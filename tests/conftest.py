import os

import pytest
from flask_migrate import upgrade
from sqlalchemy import text

from app import create_app
from app.extensions import db

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")


@pytest.fixture(scope="session")
def _schema():
    """Drop everything in the test DB and rebuild it from the migrations."""
    app = create_app("testing")
    with app.app_context():
        db.session.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        db.session.execute(text("CREATE SCHEMA public"))
        db.session.commit()
        upgrade(directory=MIGRATIONS_DIR)
        db.session.remove()
        db.engine.dispose()


@pytest.fixture()
def app(_schema):
    app = create_app("testing")
    yield app
    with app.app_context():
        db.session.remove()
        db.session.execute(text("TRUNCATE TABLE bookings, payouts, payment_attempts, booking_intents, blocked_periods, weekly_breaks, weekly_availability, services, payment_accounts, businesses, password_reset_otps, users RESTART IDENTITY CASCADE"))
        db.session.commit()


@pytest.fixture()
def client(app):
    return app.test_client()
