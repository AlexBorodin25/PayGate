"""add failed fulfillment status

Revision ID: 04749721fb40
Revises: d996af08905f
Create Date: 2026-08-05 11:46:28.923189

"""

from alembic import op

revision = "04749721fb40"
down_revision = "d996af08905f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE fulfillment_status ADD VALUE IF NOT EXISTS 'failed'")


def downgrade() -> None:
    pass
