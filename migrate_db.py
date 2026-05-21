"""
Одноразовый скрипт миграции данных из SQLite в PostgreSQL.

Ожидания:
- Целевая PostgreSQL-база ДОЛЖНА быть пустой по таблицам users/devices/payments.
- DATABASE_URL в env (обязательно).
- SQLITE_PATH из env или "metron.db" по умолчанию.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timezone

import asyncpg

SQLITE_PATH = os.getenv("SQLITE_PATH", "metron.db")
PG_DSN = os.getenv("DATABASE_URL")
if not PG_DSN:
    raise RuntimeError("DATABASE_URL is not set in environment")


def safe_parse_datetime(value: str | None) -> datetime | None:
    """
    Парсит дату-время из SQLite.
    Поддерживает: ISO, YYYY-MM-DD HH:MM:SS, YYYY-MM-DD.
    Возвращает None при ошибке парсинга.
    """
    if not value:
        return None

    raw = value.strip()
    candidates = [
        raw,
        raw.replace(" ", "T"),
        raw[:10] + "T00:00:00" if len(raw) == 10 else None,
    ]

    for cand in candidates:
        if cand is None:
            continue
        try:
            return datetime.fromisoformat(cand)
        except (ValueError, TypeError):
            continue
    return None


def safe_parse_date(value: str | None) -> str | None:
    """Извлекает дату (первые 10 символов) из строки SQLite."""
    if not value:
        return None
    return value.strip()[:10]


async def migrate() -> None:
    sql_conn = sqlite3.connect(SQLITE_PATH)
    sql_conn.row_factory = sqlite3.Row
    pg_conn = await asyncpg.connect(PG_DSN)

    try:
        counts_before: dict[str, int] = {}
        counts_after: dict[str, int] = {}
        now_utc = datetime.now(timezone.utc)

        async with pg_conn.transaction():
            # ----- users -----
            users = sql_conn.execute("SELECT * FROM users").fetchall()
            counts_before["users"] = len(users)

            for u in users:
                expire = safe_parse_datetime(u["trial_expired_at"])
                last_bill = safe_parse_date(u["last_billing_date"])

                await pg_conn.execute(
                    """
                    INSERT INTO users (
                        user_id, username, expire_at, vless_link, uuid,
                        status, notified, balance, last_billing_date
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::date)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    u["user_id"],
                    u["username"],
                    expire,
                    u["vless_link"],
                    u["uuid"],
                    u["status"],
                    bool(u["notified"]),
                    u["balance"],
                    last_bill,
                )

            # ----- devices -----
            devices = sql_conn.execute("SELECT * FROM devices").fetchall()
            counts_before["devices"] = len(devices)

            for d in devices:
                created = safe_parse_datetime(d["created_at"]) or now_utc

                await pg_conn.execute(
                    """
                    INSERT INTO devices (
                        id, user_id, device_name, client_uuid,
                        vless_link, monthly_cost, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    d["id"],
                    d["user_id"],
                    d["device_name"],
                    d["client_uuid"],
                    d["vless_link"],
                    d["monthly_cost"],
                    created,
                )

            # ----- payments -----
            payments = sql_conn.execute("SELECT * FROM payments").fetchall()
            counts_before["payments"] = len(payments)

            for p in payments:
                created = safe_parse_datetime(p["created_at"]) or now_utc

                await pg_conn.execute(
                    """
                    INSERT INTO payments (
                        id, user_id, payload, telegram_charge_id,
                        provider_charge_id, amount, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    p["id"],
                    p["user_id"],
                    p["payload"],
                    p["telegram_charge_id"],
                    p["provider_charge_id"],
                    p["amount"],
                    created,
                )

        # ----- сверка row counts -----
        for table in ("users", "devices", "payments"):
            counts_after[table] = await pg_conn.fetchval(f"SELECT COUNT(*) FROM {table}")

        # ----- динамическая синхронизация sequences -----
        for table in ("devices", "payments"):
            seq_name = await pg_conn.fetchval(
                "SELECT pg_get_serial_sequence($1, 'id')",
                table,
            )
            if seq_name:
                await pg_conn.execute(
                    f"SELECT setval('{seq_name}', COALESCE((SELECT MAX(id) FROM {table}), 1), true)"
                )

        # ----- вывод итогов -----
        has_mismatch = False
        print("=" * 55)
        print("Миграция завершена.")
        print(f"{'Таблица':<12} {'SQLite':>8} {'PostgreSQL':>12} {'Статус':>12}")
        print("-" * 55)

        for table in ("users", "devices", "payments"):
            before = counts_before.get(table, 0)
            after = counts_after.get(table, 0)
            status = "✓ OK" if before == after else "✗ MISMATCH"
            if before != after:
                has_mismatch = True
            print(f"{table:<12} {before:>8} {after:>12} {status:>12}")

        print("=" * 55)

        if has_mismatch:
            raise RuntimeError(
                f"Row count mismatch detected! Counts: {counts_before} -> {counts_after}"
            )

    finally:
        sql_conn.close()
        await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())