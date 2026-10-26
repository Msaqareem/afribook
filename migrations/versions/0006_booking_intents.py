"""booking_intents (temporary slot holds before payment)

Revision ID: 0006_booking_intents
Revises: 0005_availability
Create Date: 2026-10-26

"""
from alembic import op
import sqlalchemy as sa

revision = "0006_booking_intents"
down_revision = "0005_availability"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "booking_intents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_token", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("customer_name", sa.String(length=80), nullable=False),
        sa.Column("customer_phone", sa.String(length=16), nullable=False),
        sa.Column("service_name_snapshot", sa.String(length=80), nullable=False),
        sa.Column("duration_minutes_snapshot", sa.Integer(), nullable=False),
        sa.Column("amount_kes_snapshot", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("hold_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_booking_intents_period_order")),
        sa.CheckConstraint("amount_kes_snapshot > 0", name=op.f("ck_booking_intents_amount_positive")),
        sa.CheckConstraint("duration_minutes_snapshot > 0", name=op.f("ck_booking_intents_duration_positive")),
        sa.CheckConstraint(
            "status IN ('pending_payment','paid','failed','expired','review')",
            name=op.f("ck_booking_intents_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"],
            name=op.f("fk_booking_intents_business_id_businesses"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"], ["services.id"],
            name=op.f("fk_booking_intents_service_id_services"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booking_intents")),
        sa.UniqueConstraint("public_token", name=op.f("uq_booking_intents_public_token")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_booking_intents_idempotency_key")),
    )
    op.create_index(
        "ix_booking_intents_business_start", "booking_intents",
        ["business_id", "starts_at", "status", "hold_expires_at"], unique=False,
    )
    op.create_index("ix_booking_intents_status_hold", "booking_intents", ["status", "hold_expires_at"], unique=False)
    op.execute("ALTER TABLE booking_intents ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_index("ix_booking_intents_status_hold", table_name="booking_intents")
    op.drop_index("ix_booking_intents_business_start", table_name="booking_intents")
    op.drop_table("booking_intents")
