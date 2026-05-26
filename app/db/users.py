""" 
Запросы к таблице users.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.db.constants import UserStatus
from app.db.core import get_db

# Оставляем для обратной совместимости со старым кодом который импортирует кортеж.
# Новый код должен использовать UserStatus напрямую.
USER_STATUSES = tuple(s.value for s in UserStatus)


async def ensure_user_stub(user_id: int, username: str) -> None:
    """Создать запись пользователя если её нет. Не трогает существующие поля."""
    async with get_db().transaction() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username, status, notified, balance)
            VALUES ($1, $2, 'TRIAL', FALSE, 0)
            ON CONFLICT (user_id) DO UPDATE SET
                username = COALESCE(users.username, EXCLUDED.username)
            """,
            user_id,
            username,
        )


async def save_user(
    user_id: int,
    username: str,
    expire_at: Optional[datetime],
    vless_link: Optional[str],
    uuid_val: Optional[str],
    status: str = UserStatus.TRIAL,
) -> None:
    """
    Upsert пользователя.

    Флаги notified / low_balance_notified устанавливаются в FALSE
    ТОЛЬКО при первом INSERT. При обновлении существующей записи
    флаги не трогаются — иначе пользователь получит повторные уведомления.
    """
    async with get_db().transaction() as conn:
        await conn.execute(
            """
            INSERT INTO users (
                user_id, username, expire_at, vless_link, uuid, status, notified, low_balance_notified
            )
            VALUES ($1, $2, $3, $4, $5, $6, FALSE, FALSE)
            ON CONFLICT (user_id) DO UPDATE SET
                username   = EXCLUDED.username,
                expire_at  = EXCLUDED.expire_at,
                vless_link = EXCLUDED.vless_link,
                uuid       = EXCLUDED.uuid,
                status     = EXCLUDED.status
            -- notified и low_balance_notified намеренно не обновляются
            """,
            user_id, username, expire_at, vless_link, uuid_val, status,
        )


async def save_paid_access(
    user_id: int,
    username: str,
    vless_link: str,
    uuid_val: str,
) -> None:
    """
    Активировать платный доступ.

    Флаги уведомлений не сбрасываются при апдейте — сброс нотификаций
    об активации делается явно через set_low_balance_notified / set_notified.
    """
    async with get_db().transaction() as conn:
        await conn.execute(
            """
            INSERT INTO users (
                user_id, username, vless_link, uuid, status, notified, low_balance_notified
            )
            VALUES ($1, $2, $3, $4, 'ACTIVE', FALSE, FALSE)
            ON CONFLICT (user_id) DO UPDATE SET
                username   = EXCLUDED.username,
                vless_link = EXCLUDED.vless_link,
                uuid       = EXCLUDED.uuid,
                status     = 'ACTIVE'
            -- notified и low_balance_notified намеренно не обновляются
            """,
            user_id, username, vless_link, uuid_val,
        )


async def get_user_data_dict(user_id: int) -> Optional[Dict[str, Any]]:
    async with get_db().connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                expire_at,
                vless_link,
                uuid,
                username,
                status,
                balance,
                notified,
                low_balance_notified
            FROM users
            WHERE user_id = $1
            """,
            user_id,
        )
        return dict(row) if row else None


async def get_user_balance(user_id: int) -> Optional[Decimal]:
    db = get_db()
    async with db.connection() as conn:
        row = await conn.fetchrow(
            "SELECT balance FROM users WHERE user_id = $1",
            user_id,
        )
        return db._cents_to_rubles(row["balance"]) if row else None


async def get_user_by_uuid(uuid_val: str) -> Optional[Dict[str, Any]]:
    async with get_db().connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT user_id, username, expire_at, vless_link, uuid,
                   status, notified, low_balance_notified, balance, last_billing_date
            FROM users
            WHERE uuid = $1
            """,
            uuid_val,
        )
        return dict(row) if row else None


async def update_user_link(
    user_id: int,
    new_link: str,
    uuid_val: Optional[str] = None,
) -> None:
    async with get_db().transaction() as conn:
        if uuid_val:
            await conn.execute(
                "UPDATE users SET vless_link = $1, uuid = $2 WHERE user_id = $3",
                new_link, uuid_val, user_id,
            )
        else:
            await conn.execute(
                "UPDATE users SET vless_link = $1 WHERE user_id = $2",
                new_link, user_id,
            )


async def update_user_status(user_id: int, status: UserStatus | str) -> bool:
    """
    Обновить статус пользователя.
    Принимает UserStatus enum или строку для обратной совместимости.
    """
    status_val = status.value if isinstance(status, UserStatus) else status
    if status_val not in USER_STATUSES:
        raise ValueError(f"Invalid status: {status_val!r}. Must be one of {USER_STATUSES}")
    async with get_db().transaction() as conn:
        result = await conn.execute(
            "UPDATE users SET status = $1 WHERE user_id = $2",
            status_val, user_id,
        )
        return result != "UPDATE 0"


async def deactivate_panel_client(client_uuid: str) -> bool:
    """Деактивировать клиента по UUID. Использует INACTIVE напрямую — намеренно."""
    async with get_db().transaction() as conn:
        result = await conn.execute(
            "UPDATE users SET status = $1 WHERE uuid = $2",
            UserStatus.INACTIVE.value, client_uuid,
        )
        return result != "UPDATE 0"


async def set_low_balance_notified(user_id: int, value: bool) -> None:
    async with get_db().transaction() as conn:
        await conn.execute(
            "UPDATE users SET low_balance_notified = $1 WHERE user_id = $2",
            value, user_id,
        )


async def set_reactivation_notification_pending(user_id: int, value: bool) -> None:
    async with get_db().transaction() as conn:
        await conn.execute(
            "UPDATE users SET reactivation_notification_pending = $1 WHERE user_id = $2",
            value, user_id,
        )


async def get_expired_trial_users() -> List[int]:
    async with get_db().connection() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id
            FROM users
            WHERE status = 'TRIAL'
              AND expire_at IS NOT NULL
              AND expire_at < NOW()
            """
        )
        return [r["user_id"] for r in rows]
