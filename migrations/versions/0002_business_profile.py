"""business profile: categories, businesses, payment_accounts

Revision ID: 0002_business_profile
Revises: 0001_initial
Create Date: 2026-10-25

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_business_profile"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

STARTER_CATEGORIES = [
    ("Salon", "salon"),
    ("Barber", "barber"),
    ("Beauty & Makeup", "beauty-makeup"),
    ("Spa & Massage", "spa-massage"),
    ("Clinic", "clinic"),
    ("Dental", "dental"),
    ("Mechanic & Car Care", "mechanic-car-care"),
    ("Photography", "photography"),
    ("Tutoring", "tutoring"),
    ("Fitness & Training", "fitness-training"),
    ("Other", "other"),
]


def upgrade():
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("slug", name=op.f("uq_categories_slug")),
    )
    op.create_table(
        "businesses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("contact_phone", sa.String(length=16), nullable=False),
        sa.Column("county", sa.String(length=40), nullable=False),
        sa.Column("town", sa.String(length=60), nullable=False),
        sa.Column("address", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("logo_path", sa.String(length=255), nullable=True),
        sa.Column("is_public", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'active'"), nullable=False),
        sa.Column("timezone", sa.String(length=40), server_default=sa.text("'Africa/Nairobi'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'suspended')", name=op.f("ck_businesses_status_valid")),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug) BETWEEN 3 AND 40",
            name=op.f("ck_businesses_slug_format"),
        ),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name=op.f("fk_businesses_category_id_categories")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name=op.f("fk_businesses_owner_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_businesses")),
        sa.UniqueConstraint("owner_user_id", name=op.f("uq_businesses_owner_user_id")),
        sa.UniqueConstraint("slug", name=op.f("uq_businesses_slug")),
    )
    op.create_index(
        "ix_businesses_directory",
        "businesses",
        ["category_id", "county", "town", "is_public", "status"],
        unique=False,
    )
    op.create_table(
        "payment_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("payment_type", sa.String(length=8), nullable=False),
        sa.Column("shortcode", sa.String(length=10), nullable=False),
        sa.Column("account_reference_template", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("payment_type IN ('TILL', 'PAYBILL')", name=op.f("ck_payment_accounts_type_valid")),
        sa.CheckConstraint(
            "status IN ('pending', 'verified', 'disabled')", name=op.f("ck_payment_accounts_status_valid")
        ),
        sa.CheckConstraint("shortcode ~ '^[0-9]{5,7}$'", name=op.f("ck_payment_accounts_shortcode_format")),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"],
            name=op.f("fk_payment_accounts_business_id_businesses"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_accounts")),
        sa.UniqueConstraint("business_id", name=op.f("uq_payment_accounts_business_id")),
    )

    # Starter categories (admins manage the list from Bucket 9).
    categories = sa.table(
        "categories",
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(
        categories,
        [{"name": name, "slug": slug, "sort_order": i * 10} for i, (name, slug) in enumerate(STARTER_CATEGORIES, 1)],
    )

    # Block Supabase's public Data API from these tables (the Flask app is the table owner).
    for table in ("categories", "businesses", "payment_accounts"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("payment_accounts")
    op.drop_index("ix_businesses_directory", table_name="businesses")
    op.drop_table("businesses")
    op.drop_table("categories")
