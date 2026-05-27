"""
Запросы к таблице payments.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.db.core import get_db

PAYMENT_STATUS_RECEIVED = "RECEIVED"
PAYMENT_STATUS_APPLIED = "APPLIED"


async def record_payment_idempotent(
    user_id: int,
    payload: str,
    telegram_charge_id: str,
    provider_charge_id: str,
    amount_cents: int,
) -> Dict[str, Any]:
    """
    Идемпотентно сохранить факт получения платежного события.

    Стратегия:
    1) INSERT ... ON CONFLICT DO NOTHING со статусом RECEIVED.
       - Вернул строку -> новый платёж.
       - Не вернул -> платёж уже есть, читаем существующий.
    2) Платёж в этом шаге не применяем к балансу.
       Это делает отдельная функция apply_payment_topup_idempotent(),
       чтобы повторный webhook мог безопасно дозавершить операцию после падения.
    """
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")

    payment_key = f"payment:{provider_charge_id}"

    async with get_db().transaction() as conn:
        inserted = await conn.fetchrow(
            """
            INSERT INTO payments (
                user_id, payload, telegram_charge_id, provider_charge_id,
                amount, status, idempotency_key
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT DO NOTHING
            RETURNING
                id, user_id, payload,
                telegram_charge_id, provider_charge_id,
                amount, status, created_at, idempotency_key
            """,
            user_id,
            payload,
            telegram_charge_id,
            provider_charge_id,
            amount_cents,
            PAYMENT_STATUS_RECEIVED,
            payment_key,
        )
        if inserted:
            return {"created": True, "payment": dict(inserted)}

        existing = await conn.fetchrow(
            """
            SELECT
                id, user_id, payload,
                telegram_charge_id, provider_charge_id,
                amount, status, created_at, idempotency_key
            FROM payments
            WHERE telegram_charge_id = $1
               OR provider_charge_id = $2
            """,
            telegram_charge_id, provider_charge_id,
        )
        if existing:
            if existing["user_id"] != user_id or existing["amount"] != amount_cents:
                raise RuntimeError(
                    "Payment conflict detected for existing charge ids: "
                    f"expected user_id={user_id}, amount={amount_cents}, "
                    f"got user_id={existing['user_id']}, amount={existing['amount']}"
                )
            return {"created": False, "payment": dict(existing)}

        raise RuntimeError(
            "Payment idempotency invariant violated: "
            f"telegram_charge_id={telegram_charge_id}, "
            f"provider_charge_id={provider_charge_id}"
        )


async def apply_payment_topup_idempotent(payment_id: int) -> Dict[str, Any]:
    """
    Применить сохранённый платёж к балансу ровно один раз.

    Гарантии:
    - при повторном вызове баланс не будет пополнен дважды;
    - если старый код успел пополнить баланс, но не обновил статус платежа,
      операция подхватит уже существующую balance_transaction и пометит платёж как APPLIED.

    Дополнительно: если пользователь находится в статусе EXPIRED или INACTIVE
    (деньги кончились, доступ был отозван) — статус атомарно переводится в ACTIVE
    в той же транзакции что и пополнение баланса. Это закрывает окно гонки между
    шагом "начислить баланс" и шагом "обновить статус".

    Возвращает поле "status_changed": True если статус был обновлён.
    """
    # Статусы, из которых оплата должна возвращать юзера в ACTIVE
    _RECOVERABLE_STATUSES = {"EXPIRED", "INACTIVE", "NEW"}

    db = get_db()
    async with db.transaction() as conn:
        payment = await conn.fetchrow(
            """
            SELECT
                id, user_id, provider_charge_id, amount, status, idempotency_key
            FROM payments
            WHERE id = $1
            FOR UPDATE
            """,
            payment_id,
        )
        if not payment:
            raise RuntimeError(f"Payment not found: id={payment_id}")

        user_row = await conn.fetchrow(
            "SELECT balance, status FROM users WHERE user_id = $1 FOR UPDATE",
            payment["user_id"],
        )
        if not user_row:
            raise RuntimeError(
                f"Payment apply failed: user not found user_id={payment['user_id']} payment_id={payment_id}"
            )

        current_balance = user_row["balance"]
        current_status = user_row["status"]
        payment_key = payment["idempotency_key"] or f"payment:{payment['provider_charge_id']}"

        if payment["status"] == PAYMENT_STATUS_APPLIED:
            return {
                "applied": False,
                "already_applied": True,
                "status_changed": False,
                "balance": db._cents_to_rubles(current_balance),
            }

        existing_topup = await conn.fetchval(
            """
            SELECT 1
            FROM balance_transactions
            WHERE idempotency_key = $1
            """,
            payment_key,
        )
        if existing_topup:
            await conn.execute(
                """
                UPDATE payments
                SET status = $1,
                    idempotency_key = COALESCE(idempotency_key, $2)
                WHERE id = $3
                """,
                PAYMENT_STATUS_APPLIED,
                payment_key,
                payment_id,
            )
            return {
                "applied": False,
                "already_applied": True,
                "status_changed": False,
                "balance": db._cents_to_rubles(current_balance),
            }

        # Атомарно: пополняем баланс и если нужно — обновляем статус
        status_changed = current_status in _RECOVERABLE_STATUSES
        if status_changed:
            updated = await conn.fetchrow(
                """
                UPDATE users
                SET balance = balance + $1,
                    status = 'ACTIVE'
                WHERE user_id = $2
                RETURNING balance
                """,
                payment["amount"],
                payment["user_id"],
            )
        else:
            updated = await conn.fetchrow(
                """
                UPDATE users
                SET balance = balance + $1
                WHERE user_id = $2
                RETURNING balance
                """,
                payment["amount"],
                payment["user_id"],
            )
        balance_after = updated["balance"]

        await conn.execute(
            """
            INSERT INTO balance_transactions (
                user_id, kind, amount_cents,
                balance_before, balance_after,
                reference_type, reference_id, idempotency_key
            )
            VALUES ($1, 'TOPUP', $2, $3, $4, 'payment', $5, $6)
            """,
            payment["user_id"],
            payment["amount"],
            current_balance,
            balance_after,
            payment["provider_charge_id"],
            payment_key,
        )
        await conn.execute(
            """
            UPDATE payments
            SET status = $1,
                idempotency_key = COALESCE(idempotency_key, $2)
            WHERE id = $3
            """,
            PAYMENT_STATUS_APPLIED,
            payment_key,
            payment_id,
        )

        return {
            "applied": True,
            "already_applied": False,
            "status_changed": status_changed,
            "balance": db._cents_to_rubles(balance_after),
        }


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
                amount, status, created_at, idempotency_key
            FROM payments
            WHERE telegram_charge_id = $1
               OR provider_charge_id = $2
            """,
            telegram_charge_id, provider_charge_id,
        )
        return dict(row) if row else None
