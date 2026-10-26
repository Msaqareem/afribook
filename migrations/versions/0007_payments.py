"""payments: fee snapshots, payment_attempts, payouts, bookings (+ no-overlap exclusion constraint)

Revision ID: 0007_payments
Revises: 0006_booking_intents
Create Date: 2026-10-26

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_payments"
down_revision = "0006_booking_intents"
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade():
    # Needed for the exclusion constraint (equality on business_id + range overlap).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.add_column("booking_intents", sa.Column("fee_kes_snapshot", sa.Integer(), server_default="0", nullable=False))
    op.add_column("booking_intents", sa.Column("fee_bps_snapshot", sa.Integer(), server_default="0", nullable=False))
    op.create_check_constraint(op.f("ck_booking_intents_fee_not_negative"), "booking_intents", "fee_kes_snapshot >= 0")

    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("booking_intent_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("our_reference", sa.String(length=64), nullable=False),
        sa.Column("provider_reference", sa.String(length=80), nullable=True),
        sa.Column("amount_kes", sa.Integer(), nullable=False),
        sa.Column("customer_phone", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("result_code", sa.String(length=20), nullable=True),
        sa.Column("result_description", sa.String(length=200), nullable=True),
        sa.Column("mpesa_receipt", sa.String(length=40), nullable=True),
        sa.Column("callback_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("initiated_at", TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.Column("callback_received_at", TIMESTAMP, nullable=True),
        sa.Column("completed_at", TIMESTAMP, nullable=True),
        sa.Column("last_checked_at", TIMESTAMP, nullable=True),
        sa.Column("resolved_at", TIMESTAMP, nullable=True),
        sa.Column("resolution_note", sa.String(length=200), nullable=True),
        sa.Column("created_at", TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_kes > 0", name=op.f("ck_payment_attempts_amount_positive")),
        sa.CheckConstraint(
            "status IN ('initiated','pending','succeeded','failed','cancelled','timeout','review')",
            name=op.f("ck_payment_attempts_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["booking_intent_id"], ["booking_intents.id"],
            name=op.f("fk_payment_attempts_booking_intent_id_booking_intents"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_attempts")),
        sa.UniqueConstraint("our_reference", name=op.f("uq_payment_attempts_our_reference")),
        sa.UniqueConstraint("provider_reference", name=op.f("uq_payment_attempts_provider_reference")),
        sa.UniqueConstraint("mpesa_receipt", name=op.f("uq_payment_attempts_mpesa_receipt")),
    )
    op.create_index(
        "uq_payment_attempts_one_active", "payment_attempts", ["booking_intent_id"], unique=True,
        postgresql_where=sa.text("status IN ('initiated','pending')"),
    )
    op.create_index("ix_payment_attempts_status_created", "payment_attempts", ["status", "created_at"], unique=False)

    op.create_table(
        "payouts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("total_kes", sa.Integer(), nullable=False),
        sa.Column("booking_count", sa.Integer(), nullable=False),
        sa.Column("destination_snapshot", sa.String(length=200), nullable=False),
        sa.Column("mpesa_reference", sa.String(length=40), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.Column("paid_at", TIMESTAMP, nullable=True),
        sa.CheckConstraint("status IN ('pending','paid','cancelled')", name=op.f("ck_payouts_status_valid")),
        sa.CheckConstraint("total_kes >= 0 AND booking_count >= 0", name=op.f("ck_payouts_amounts_valid")),
        sa.CheckConstraint(
            "status <> 'paid' OR (paid_at IS NOT NULL AND mpesa_reference IS NOT NULL)",
            name=op.f("ck_payouts_paid_has_proof"),
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_payouts_business_id_businesses"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name=op.f("fk_payouts_created_by_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payouts")),
    )
    op.create_index("ix_payouts_business_status", "payouts", ["business_id", "status"], unique=False)

    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_reference", sa.String(length=16), nullable=False),
        sa.Column("booking_intent_id", sa.Integer(), nullable=False),
        sa.Column("payment_attempt_id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("customer_name_snapshot", sa.String(length=80), nullable=False),
        sa.Column("customer_phone_snapshot", sa.String(length=16), nullable=False),
        sa.Column("service_name_snapshot", sa.String(length=80), nullable=False),
        sa.Column("duration_minutes_snapshot", sa.Integer(), nullable=False),
        sa.Column("amount_kes_snapshot", sa.Integer(), nullable=False),
        sa.Column("fee_kes_snapshot", sa.Integer(), nullable=False),
        sa.Column("starts_at", TIMESTAMP, nullable=False),
        sa.Column("ends_at", TIMESTAMP, nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("completed_at", TIMESTAMP, nullable=True),
        sa.Column("payout_id", sa.Integer(), nullable=True),
        sa.Column("created_at", TIMESTAMP, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_bookings_period_order")),
        sa.CheckConstraint("status IN ('confirmed','completed')", name=op.f("ck_bookings_status_valid")),
        sa.CheckConstraint("amount_kes_snapshot > 0 AND fee_kes_snapshot >= 0", name=op.f("ck_bookings_amounts_valid")),
        sa.ForeignKeyConstraint(["booking_intent_id"], ["booking_intents.id"], name=op.f("fk_bookings_booking_intent_id_booking_intents"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_attempt_id"], ["payment_attempts.id"], name=op.f("fk_bookings_payment_attempt_id_payment_attempts"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_bookings_business_id_businesses"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], name=op.f("fk_bookings_service_id_services"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payout_id"], ["payouts.id"], name=op.f("fk_bookings_payout_id_payouts"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
        sa.UniqueConstraint("public_reference", name=op.f("uq_bookings_public_reference")),
        sa.UniqueConstraint("booking_intent_id", name=op.f("uq_bookings_booking_intent_id")),
        sa.UniqueConstraint("payment_attempt_id", name=op.f("uq_bookings_payment_attempt_id")),
    )
    op.create_index("ix_bookings_business_start", "bookings", ["business_id", "starts_at", "status"], unique=False)
    op.create_index("ix_bookings_payout", "bookings", ["payout_id"], unique=False)
    # Two bookings for the same business may never overlap in time (half-open range, so back-to-back is fine).
    op.execute(
        "ALTER TABLE bookings ADD CONSTRAINT ex_bookings_no_overlap "
        "EXCLUDE USING gist (business_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)"
    )

    for table in ("payment_attempts", "payouts", "bookings"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("bookings")
    op.drop_table("payouts")
    op.drop_index("ix_payment_attempts_status_created", table_name="payment_attempts")
    op.drop_index("uq_payment_attempts_one_active", table_name="payment_attempts")
    op.drop_table("payment_attempts")
    op.drop_constraint(op.f("ck_booking_intents_fee_not_negative"), "booking_intents", type_="check")
    op.drop_column("booking_intents", "fee_bps_snapshot")
    op.drop_column("booking_intents", "fee_kes_snapshot")
