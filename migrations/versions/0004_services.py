"""services

Revision ID: 0004_services
Revises: 0003_drop_business_town
Create Date: 2026-10-25

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_services"
down_revision = "0003_drop_business_town"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("price_kes", sa.Integer(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("price_kes > 0", name=op.f("ck_services_price_positive")),
        sa.CheckConstraint("duration_minutes > 0", name=op.f("ck_services_duration_positive")),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], name=op.f("fk_services_business_id_businesses"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_services")),
    )
    op.create_index("ix_services_business_active", "services", ["business_id", "is_active"], unique=False)
    op.execute("ALTER TABLE services ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_index("ix_services_business_active", table_name="services")
    op.drop_table("services")
