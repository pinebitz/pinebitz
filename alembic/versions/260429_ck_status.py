"""status check constraints

Revision ID: 260429_ck_status
Revises: 20260429_add_plan_status
Create Date: 2026-04-29
"""

from __future__ import annotations

from alembic import op

revision = "260429_ck_status"
down_revision = "20260429_add_plan_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_exchange_connections_status",
        "exchange_connections",
        "status IN ('active','paused','error','deleted')",
    )
    op.create_check_constraint(
        "ck_bot_plans_status",
        "bot_plans",
        "status IN ('active','paused','deleted')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_bot_plans_status", "bot_plans", type_="check")
    op.drop_constraint("ck_exchange_connections_status", "exchange_connections", type_="check")
