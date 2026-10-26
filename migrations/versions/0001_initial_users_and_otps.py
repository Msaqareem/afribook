"""initial: users and password_reset_otps

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-21

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phone_e164", sa.String(length=16), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'admin')", name=op.f("ck_users_role_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("phone_e164", name=op.f("uq_users_phone_e164")),
    )
    op.create_table(
        "password_reset_otps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("phone_e164", sa.String(length=16), nullable=False),
        sa.Column("purpose", sa.String(length=24), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('signup_verify', 'password_reset')",
            name=op.f("ck_password_reset_otps_purpose_valid"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_password_reset_otps_attempts_nonnegative")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_password_reset_otps_user_id_users"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_otps")),
    )
    op.create_index(
        "ix_password_reset_otps_lookup",
        "password_reset_otps",
        ["phone_e164", "purpose", "created_at"],
        unique=False,
    )
    # Supabase exposes the public schema through its Data API. Enabling RLS with no
    # policies blocks that API; the Flask app connects as the table owner and is unaffected.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE password_reset_otps ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_index("ix_password_reset_otps_lookup", table_name="password_reset_otps")
    op.drop_table("password_reset_otps")
    op.drop_table("users")
