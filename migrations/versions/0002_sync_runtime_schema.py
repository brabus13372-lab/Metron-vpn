"""Sync stamped databases with runtime schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-25

Убирает расхождение между старой runtime-DDL и Alembic-миграциями.
После этой ревизии единственным источником правды для схемы остаётся Alembic.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE balance_transactions
            DROP CONSTRAINT IF EXISTS balance_transactions_kind_check;

        ALTER TABLE balance_transactions
            ADD CONSTRAINT balance_transactions_kind_check
            CHECK (kind IN (
                'TOPUP',
                'DAILY_CHARGE',
                'CHARGE',
                'ADMIN_ADJUSTMENT',
                'REFUND',
                'BONUS'
            ));
    """)


def downgrade() -> None:
    bind = op.get_bind()
    charge_rows = bind.execute(
        sa.text("SELECT COUNT(*) FROM balance_transactions WHERE kind = 'CHARGE'")
    ).scalar_one()
    if charge_rows:
        raise RuntimeError(
            "Cannot downgrade revision 0002 while balance_transactions contains CHARGE rows."
        )

    op.execute("""
        ALTER TABLE balance_transactions
            DROP CONSTRAINT IF EXISTS balance_transactions_kind_check;

        ALTER TABLE balance_transactions
            ADD CONSTRAINT balance_transactions_kind_check
            CHECK (kind IN (
                'TOPUP',
                'DAILY_CHARGE',
                'ADMIN_ADJUSTMENT',
                'REFUND',
                'BONUS'
            ));
    """)
