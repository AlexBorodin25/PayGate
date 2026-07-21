"""rename product quantity column

Revision ID: 3165a584a679
Revises: 90a44d7add26
Create Date: 2026-07-21 11:21:23.235745

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3165a584a679"
down_revision: str | Sequence[str] | None = "90a44d7add26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("products", "quantity_in_stock", new_column_name="quantity")


def downgrade() -> None:
    op.alter_column("products", "quantity", new_column_name="quantity_in_stock")
