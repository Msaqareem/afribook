"""drop businesses.town; address becomes the single free-text location

Product decision 2026-10-25: owners pick a county and describe the location in one field
(area, street or landmark), instead of separate town and address fields.

Revision ID: 0003_drop_business_town
Revises: 0002_business_profile
Create Date: 2026-10-25

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_drop_business_town"
down_revision = "0002_business_profile"
branch_labels = None
depends_on = None


def upgrade():
    # Keep any town an owner already entered by folding it into the location text.
    op.execute(
        "UPDATE businesses SET address = LEFT(address || ', ' || town, 200) "
        "WHERE town IS NOT NULL AND town <> ''"
    )
    op.drop_index("ix_businesses_directory", table_name="businesses")
    op.drop_column("businesses", "town")
    op.create_index(
        "ix_businesses_directory", "businesses", ["category_id", "county", "is_public", "status"], unique=False
    )


def downgrade():
    op.drop_index("ix_businesses_directory", table_name="businesses")
    op.add_column("businesses", sa.Column("town", sa.String(length=60), server_default="", nullable=False))
    op.alter_column("businesses", "town", server_default=None)
    op.create_index(
        "ix_businesses_directory",
        "businesses",
        ["category_id", "county", "town", "is_public", "status"],
        unique=False,
    )
