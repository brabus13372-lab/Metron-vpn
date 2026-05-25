"""
Запросы связанные с балансом и ежедневным биллингом.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

import asyncpg

from app.db.core import get_db


async def _create_balance_transaction(
    conn: asyncpg.Connection,
    user_id: int,
    kind: str,
    amount_cents: int,
    balance_before: int,
    balance_after: int,
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO balance_transactions (
            user_id, kind, amount_cents,
            balance_before, balance_after,
            reference_type, reference_id, idempotency_key
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id
        """,
        user_id, kind, amount_cents,
        balance_before, balance_after,
        reference_type, reference_id, idempotency_key,
    )
    return row["id"]


async def add_balance_atomic(
    user_id: int,
    amount_cents: int,
    reference_type: Optional[str] = "payment",
    reference_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Optional[Decimal]:
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")

    db = get_db()
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
            user_id,
        )
        if not row:
            return None

        balance_before = row["balance"]
        updated = await conn.fetchrow(
            """
            UPDATE users
            SET balance = balance + $1
            WHERE user_id = $2
            RETURNING balance
            """,
            amount_cents, user_id,
        )
        balance_after = updated["balance"]

        await _create_balance_transaction(
            conn=conn,
            user_id=user_id,
            kind="TOPUP",
            amount_cents=amount_cents,
            balance_before=balance_before,
            balance_after=balance_after,
            reference_type=reference_type,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
        )

        return db._cents_to_rubles(balance_after)


async def charge_balance_atomic(
    user_id: int,
    amount_cents: int,
    reference_type: Optional[str] = "manual_charge",
    reference_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")

    db = get_db()
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
            user_id,
        )
        if not row:
            return None

        balance_before = row["balance"]
        if balance_before < amount_cents:
            return {
                "charged": False,
                "insufficient_funds": True,
                "balance_before": db._cents_to_rubles(balance_before),
                "balance_after": db._cents_to_rubles(balance_before),
            }

        updated = await conn.fetchrow(
            """
            UPDATE users
            SET balance = balance - $1
            WHERE user_id = $2 AND balance >= $1
            RETURNING balance
            """,
            amount_cents, user_id,
        )
        if not updated:
            return {
                "charged": False,
                "insufficient_funds": True,
                "balance_before": db._cents_to_rubles(balance_before),
                "balance_after": db._cents_to_rubles(balance_before),
            }

        balance_after = updated["balance"]

        await _create_balance_transaction(
            conn=conn,
            user_id=user_id,
            kind="CHARGE",
            amount_cents=amount_cents,
            balance_before=balance_before,
            balance_after=balance_after,
            reference_type=reference_type,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
        )

        return {
            "charged": True,
            "insufficient_funds": False,
            "balance_before": db._cents_to_rubles(balance_before),
            "balance_after": db._cents_to_rubles(balance_after),
        }


async def charge_daily_billing_atomic(
    user_id: int,
    amount_cents: int,
    billing_date: date,
) -> Optional[Dict[str, Any]]:
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")

    db = get_db()
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "SELECT balance, last_billing_date FROM users WHERE user_id = $1 FOR UPDATE",
            user_id,
        )
        if not row:
            return None

        balance_before = row["balance"]
        last_bill = row["last_billing_date"]

        if last_bill is not None and last_bill == billing_date:
            return {
                "charged": False,
                "already_charged_today": True,
                "insufficient_funds": False,
                "balance_before": db._cents_to_rubles(balance_before),
                "balance_after": db._cents_to_rubles(balance_before),
            }

        if balance_before < amount_cents:
            return {
                "charged": False,
                "already_charged_today": False,
                "insufficient_funds": True,
                "balance_before": db._cents_to_rubles(balance_before),
                "balance_after": db._cents_to_rubles(balance_before),
            }

        updated = await conn.fetchrow(
            """
            UPDATE users
            SET balance = balance - $1, last_billing_date = $2
            WHERE user_id = $3 AND balance >= $1
            RETURNING balance
            """,
            amount_cents, billing_date, user_id,
        )
        if not updated:
            return {
                "charged": False,
                "already_charged_today": False,
                "insufficient_funds": True,
                "balance_before": db._cents_to_rubles(balance_before),
                "balance_after": db._cents_to_rubles(balance_before),
            }

        balance_after = updated["balance"]

        await _create_balance_transaction(
            conn=conn,
            user_id=user_id,
            kind="DAILY_CHARGE",
            amount_cents=amount_cents,
            balance_before=balance_before,
            balance_after=balance_after,
            reference_type="billing",
            reference_id=str(billing_date),
            idempotency_key=f"daily:{user_id}:{billing_date.isoformat()}",
        )

        return {
            "charged": True,
            "already_charged_today": False,
            "insufficient_funds": False,
            "balance_before": db._cents_to_rubles(balance_before),
            "balance_after": db._cents_to_rubles(balance_after),
        }


async def set_user_balance(user_id: int, amount_cents: int) -> bool:
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")

    db = get_db()
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
            user_id,
        )
        if not row:
            return False

        balance_before = row["balance"]
        balance_after = amount_cents

        await conn.execute(
            "UPDATE users SET balance = $1 WHERE user_id = $2",
            balance_after, user_id,
        )

        # Не пишем транзакцию если баланс не изменился
        if balance_after == balance_before:
            return True

        if balance_after > balance_before:
            ref_type = "admin_set_balance_increase"
        else:
            ref_type = "admin_set_balance_decrease"

        await _create_balance_transaction(
            conn=conn,
            user_id=user_id,
            kind="ADMIN_ADJUSTMENT",
            amount_cents=abs(balance_after - balance_before),
            balance_before=balance_before,
            balance_after=balance_after,
            reference_type=ref_type,
        )

        return True


async def update_user_last_billing_date(user_id: int, billing_date: date) -> None:
    async with get_db().transaction() as conn:
        await conn.execute(
            "UPDATE users SET last_billing_date = $1 WHERE user_id = $2",
            billing_date, user_id,
        )


async def get_user_last_billing_date(user_id: int) -> Optional[date]:
    async with get_db().connection() as conn:
        row = await conn.fetchrow(
            "SELECT last_billing_date FROM users WHERE user_id = $1",
            user_id,
        )
        return row["last_billing_date"] if row else None


async def get_balance_transactions(
    user_id: int,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    db = get_db()
    async with db.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id, user_id, kind, amount_cents,
                balance_before, balance_after,
                reference_type, reference_id,
                idempotency_key, created_at
            FROM balance_transactions
            WHERE user_id = $1
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            user_id, limit,
        )
        return [
            {
                **dict(r),
                "amount": db._cents_to_rubles(r["amount_cents"]),
                "balance_before_rub": db._cents_to_rubles(r["balance_before"]),
                "balance_after_rub": db._cents_to_rubles(r["balance_after"]),
            }
            for r in rows
        ]
