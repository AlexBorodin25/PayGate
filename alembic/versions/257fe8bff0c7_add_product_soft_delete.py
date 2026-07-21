"""add product soft delete

Revision ID: 257fe8bff0c7
Revises: 3165a584a679
Create Date: 2026-07-21 11:46:56.833446

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "257fe8bff0c7"
down_revision: str | Sequence[str] | None = "3165a584a679"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("products", "is_deleted", server_default=None)


def downgrade() -> None:
    op.drop_column("products", "is_deleted")
