"""exchange_connections + bot_plans

Revision ID: 20260429_initial
Revises:
Create Date: 2026-04-29

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

revision = "20260429_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "exchange_connections",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_key", sa.String(length=255), nullable=True),
        sa.Column("label", sa.String(length=512), nullable=False),
        sa.Column("venue", sa.String(length=64), nullable=False),
        sa.Column("market_lane", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("credential_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default=sa.text("'active'")),
        sa.Column("extra", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "bot_plans",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "connection_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("exchange_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("instrument_kind", sa.String(length=32), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "config_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("bot_plans")
    op.drop_table("exchange_connections")
