"""Add explicit notification state flags

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-26

Separates early low-balance, critical low-balance, and reactivation
notifications so they no longer share one boolean flag.
"""
from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS low_balance_critical_notified BOOLEAN NOT NULL DEFAULT FALSE;

        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS reactivation_notification_pending BOOLEAN NOT NULL DEFAULT FALSE;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE users
            DROP COLUMN IF EXISTS reactivation_notification_pending;

        ALTER TABLE users
            DROP COLUMN IF EXISTS low_balance_critical_notified;
    """)
