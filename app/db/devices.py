"""
Запросы к таблице devices.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.db.core import get_db


async def add_device(
    user_id: int,
    device_name: str,
    client_uuid: str,
    vless_link: str,
    monthly_cost_cents: int = 10000,
) -> int:
    """
    Добавить устройство, вернуть его id.
    При отсутствии user_id asyncpg выбросит ForeignKeyViolationError.
    """
    async with get_db().transaction() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO devices (user_id, device_name, client_uuid, vless_link, monthly_cost)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            user_id, device_name, client_uuid, vless_link, monthly_cost_cents,
        )
        return row["id"]


async def remove_device(device_id: int, user_id: Optional[int] = None) -> int:
    async with get_db().transaction() as conn:
        if user_id is not None:
            result = await conn.execute(
                "DELETE FROM devices WHERE id = $1 AND user_id = $2",
                device_id, user_id,
            )
        else:
            result = await conn.execute(
                "DELETE FROM devices WHERE id = $1",
                device_id,
            )
        return int(result.split()[-1])


async def deactivate_device(
    device_id: int,
    user_id: Optional[int] = None,
    reason: Optional[str] = None,
) -> bool:
    async with get_db().transaction() as conn:
        if user_id is not None:
            result = await conn.execute(
                """
                UPDATE devices
                SET is_active = FALSE, disabled_at = NOW(), disabled_reason = $1
                WHERE id = $2 AND user_id = $3
                """,
                reason, device_id, user_id,
            )
        else:
            result = await conn.execute(
                """
                UPDATE devices
                SET is_active = FALSE, disabled_at = NOW(), disabled_reason = $1
                WHERE id = $2
                """,
                reason, device_id,
            )
        return result != "UPDATE 0"


async def activate_device(
    device_id: int,
    user_id: Optional[int] = None,
) -> bool:
    async with get_db().transaction() as conn:
        if user_id is not None:
            result = await conn.execute(
                """
                UPDATE devices
                SET is_active = TRUE, disabled_at = NULL, disabled_reason = NULL
                WHERE id = $1 AND user_id = $2
                """,
                device_id, user_id,
            )
        else:
            result = await conn.execute(
                """
                UPDATE devices
                SET is_active = TRUE, disabled_at = NULL, disabled_reason = NULL
                WHERE id = $1
                """,
                device_id,
            )
        return result != "UPDATE 0"


async def get_user_devices(user_id: int) -> List[Dict[str, Any]]:
    db = get_db()
    async with db.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id, device_name, client_uuid, vless_link,
                monthly_cost, created_at,
                is_active, disabled_at, disabled_reason
            FROM devices
            WHERE user_id = $1
            ORDER BY created_at DESC
            """,
            user_id,
        )
        return [
            {**dict(r), "monthly_cost": db._cents_to_rubles(r["monthly_cost"])}
            for r in rows
        ]


async def get_device_by_id(device_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    async with db.connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                id, user_id, device_name, client_uuid, vless_link,
                monthly_cost, created_at,
                is_active, disabled_at, disabled_reason
            FROM devices
            WHERE id = $1
            """,
            device_id,
        )
        if not row:
            return None
        return {**dict(row), "monthly_cost": db._cents_to_rubles(row["monthly_cost"])}


async def update_device_link(
    device_id: int,
    user_id: int,
    new_uuid: str,
    new_link: str,
) -> bool:
    async with get_db().transaction() as conn:
        result = await conn.execute(
            """
            UPDATE devices
            SET client_uuid = $1, vless_link = $2
            WHERE id = $3 AND user_id = $4
            """,
            new_uuid, new_link, device_id, user_id,
        )
        return result != "UPDATE 0"


async def get_all_users_with_devices() -> List[int]:
    async with get_db().connection() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT d.user_id
            FROM devices d
            JOIN users u ON u.user_id = d.user_id
            WHERE u.status = 'ACTIVE'
              AND d.is_active = TRUE
            ORDER BY d.user_id
            """
        )
        return [r["user_id"] for r in rows]


async def get_user_total_monthly_cost(user_id: int) -> Decimal:
    db = get_db()
    async with db.connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(monthly_cost), 0) AS total
            FROM devices
            WHERE user_id = $1
              AND is_active = TRUE
            """,
            user_id,
        )
        return db._cents_to_rubles(row["total"])
