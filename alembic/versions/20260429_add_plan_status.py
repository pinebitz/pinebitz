"""add status to bot_plans

Revision ID: 20260429_add_plan_status
Revises: 20260429_initial
Create Date: 2026-04-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260429_add_plan_status"
down_revision = "20260429_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_plans",
        sa.Column("status", sa.String(length=64), nullable=False, server_default=sa.text("'active'")),
    )


def downgrade() -> None:
    op.drop_column("bot_plans", "status")
