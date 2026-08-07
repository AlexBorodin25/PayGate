"""add product image url

Revision ID: 24dbc4cf8921
Revises: 04749721fb40
Create Date: 2026-08-07 11:24:27.841461

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "replace_with_revision_id"
down_revision: str | Sequence[str] | None = "replace_with_previous_revision_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("image_url", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "image_url")