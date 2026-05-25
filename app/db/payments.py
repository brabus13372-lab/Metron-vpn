"""
Запросы к таблице payments.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.db.core import get_db


async def record_payment_idempotent(
    user_id: int,
    payload: str,
    telegram_charge_id: str,
    provider_charge_id: str,
    amount_cents: int,
) -> Dict[str, Any]:
    """
    Идемпотентная запись платежа.

    Стратегия:
    1) INSERT ... ON CONFLICT DO NOTHING.
       - Вернул строку → новый платёж.
       - Не вернул → конфликт по уникальному индексу, читаем существующий.
    2) Если по каким-то причинам не нашли существующий после конфликта — RuntimeError
       (инвариант нарушен, нужно разбираться вручную).
    """
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")

    async with get_db().transaction() as conn:
        inserted = await conn.fetchrow(
            """
            INSERT INTO payments (
                user_id, payload, telegram_charge_id, provider_charge_id, amount
            )
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT DO NOTHING
            RETURNING
                id, user_id, payload,
                telegram_charge_id, provider_charge_id,
                amount, created_at
            """,
            user_id, payload, telegram_charge_id, provider_charge_id, amount_cents,
        )
        if inserted:
            return {"created": True, "payment": dict(inserted)}

        existing = await conn.fetchrow(
            """
            SELECT
                id, user_id, payload,
                telegram_charge_id, provider_charge_id,
                amount, created_at
            FROM payments
            WHERE telegram_charge_id = $1
               OR provider_charge_id = $2
            """,
            telegram_charge_id, provider_charge_id,
        )
        if existing:
            return {"created": False, "payment": dict(existing)}

        raise RuntimeError(
            "Payment idempotency invariant violated: "
            f"telegram_charge_id={telegram_charge_id}, "
            f"provider_charge_id={provider_charge_id}"
        )


async def get_payment_by_charge_ids(
    telegram_charge_id: str,
    provider_charge_id: str,
) -> Optional[Dict[str, Any]]:
    async with get_db().connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                id, user_id, payload,
                telegram_charge_id, provider_charge_id,
                amount, created_at
            FROM payments
            WHERE telegram_charge_id = $1
               OR provider_charge_id = $2
            """,
            telegram_charge_id, provider_charge_id,
        )
        return dict(row) if row else None
