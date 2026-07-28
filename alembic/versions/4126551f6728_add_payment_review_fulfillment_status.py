"""add payment review fulfillment status

Revision ID: 4126551f6728
Revises: 080ad7c02487
Create Date: 2026-07-27 17:14:48.949699

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4126551f6728"
down_revision: str | Sequence[str] | None = "080ad7c02487"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE fulfillment_status ADD VALUE IF NOT EXISTS 'payment_review'")


def downgrade() -> None:
    pass
