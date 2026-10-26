from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect

from app.extensions import db
from tests.conftest import MIGRATIONS_DIR


def test_tables_and_constraints_exist(app):
    with app.app_context():
        insp = inspect(db.engine)
        assert {"users", "password_reset_otps"} <= set(insp.get_table_names())
        uniques = {u["name"] for u in insp.get_unique_constraints("users")}
        assert "uq_users_phone_e164" in uniques
        checks = {c["name"] for c in insp.get_check_constraints("users")}
        assert "ck_users_role_valid" in checks
        assert "ix_password_reset_otps_lookup" in {i["name"] for i in insp.get_indexes("password_reset_otps")}


def test_models_match_migrations(app):
    """No drift between SQLAlchemy models and the migration history."""
    with app.app_context(), db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), db.metadata)
    assert diff == []


def test_downgrade_then_upgrade_from_empty(app):
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
        downgrade(directory=MIGRATIONS_DIR, revision="base")
        assert "users" not in inspect(db.engine).get_table_names()
        upgrade(directory=MIGRATIONS_DIR)
        assert "users" in inspect(db.engine).get_table_names()
        db.engine.dispose()
