"""availability: weekly_availability, weekly_breaks, blocked_periods

Revision ID: 0005_availability
Revises: 0004_services
Create Date: 2026-10-25

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_availability"
down_revision = "0004_services"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "weekly_availability",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name=op.f("ck_weekly_availability_weekday_valid")),
        sa.CheckConstraint("end_time > start_time", name=op.f("ck_weekly_availability_time_order")),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"],
            name=op.f("fk_weekly_availability_business_id_businesses"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weekly_availability")),
    )
    op.create_index(
        "ix_weekly_availability_business_day", "weekly_availability", ["business_id", "weekday"], unique=False
    )

    op.create_table(
        "weekly_breaks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name=op.f("ck_weekly_breaks_weekday_valid")),
        sa.CheckConstraint("end_time > start_time", name=op.f("ck_weekly_breaks_time_order")),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"],
            name=op.f("fk_weekly_breaks_business_id_businesses"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weekly_breaks")),
    )
    op.create_index("ix_weekly_breaks_business_day", "weekly_breaks", ["business_id", "weekday"], unique=False)

    op.create_table(
        "blocked_periods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_blocked_periods_period_order")),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"],
            name=op.f("fk_blocked_periods_business_id_businesses"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_blocked_periods")),
    )
    op.create_index("ix_blocked_periods_business_start", "blocked_periods", ["business_id", "starts_at"], unique=False)

    for table in ("weekly_availability", "weekly_breaks", "blocked_periods"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_index("ix_blocked_periods_business_start", table_name="blocked_periods")
    op.drop_table("blocked_periods")
    op.drop_index("ix_weekly_breaks_business_day", table_name="weekly_breaks")
    op.drop_table("weekly_breaks")
    op.drop_index("ix_weekly_availability_business_day", table_name="weekly_availability")
    op.drop_table("weekly_availability")
