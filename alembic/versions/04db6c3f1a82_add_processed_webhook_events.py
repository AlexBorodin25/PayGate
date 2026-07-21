"""add processed webhook events

Revision ID: 04db6c3f1a82
Revises: 257fe8bff0c7
Create Date: 2026-07-21 14:06:03.281952

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "04db6c3f1a82"
down_revision: str | Sequence[str] | None = "257fe8bff0c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_webhook_events",
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )


def downgrade() -> None:
    op.drop_table("processed_webhook_events")
