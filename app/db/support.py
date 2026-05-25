"""
Запросы к таблице support_tickets.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.db.core import get_db


async def create_ticket(
    user_id: int,
    message: str,
    files: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """
    Создать обращение в поддержку.
    files — список {'name': str, 'size': int, 'type': str} (только метаданные).
    Возвращает id созданного тикета.
    """
    files_json = json.dumps(files or [])
    async with get_db().transaction() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO support_tickets (user_id, message, files)
            VALUES ($1, $2, $3::jsonb)
            RETURNING id
            """,
            user_id,
            message.strip(),
            files_json,
        )
        return row["id"]


async def get_user_tickets(
    user_id: int,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Вернуть историю обращений пользователя, последние первыми."""
    async with get_db().connection() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id, message, status,
                files, created_at, answered_at
            FROM support_tickets
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
        return [dict(r) for r in rows]
