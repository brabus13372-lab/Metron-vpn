"""
Одноразовый скрипт миграции данных из SQLite (старая схема) в PostgreSQL (новая схема).

Старая SQLite-схема (metron.db):
    users(user_id, username, trial_expired_at, vless_link, uuid, status, notified)
    Статусы: TRIAL, PAID, EXPIRED

Новая PostgreSQL-схема:
    users(user_id, username, expire_at, vless_link, uuid,
          status, notified, balance, last_billing_date, low_balance_notified)
    Статусы: NEW, TRIAL, ACTIVE, INACTIVE, EXPIRED
    devices(id, user_id, device_name, client_uuid, vless_link, monthly_cost, ...)
    payments — в старой базе нет, пропускаем

Ожидания:
- Целевая PostgreSQL-база ДОЛЖНА быть пустой по таблицам users/devices/payments.
- DATABASE_URL в env (обязательно).
- SQLITE_PATH из env или "metron.db" по умолчанию.
- DAILY_COST_CENTS из env или 1000 (10 руб/день) по умолчанию.
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

# Стоимость одного дня в копейках
DAILY_COST_CENTS = int(os.getenv("DAILY_COST_CENTS", "1000"))  # default: 10 руб/день

# Статусы допустимые в новой схеме
VALID_STATUSES = {"NEW", "TRIAL", "ACTIVE", "INACTIVE", "EXPIRED"}

# Маппинг старых статусов в новые
STATUS_MAP = {
    "TRIAL": "TRIAL",
    "PAID": "ACTIVE",    # в старой базе был PAID, в новой это ACTIVE
    "ACTIVE": "ACTIVE",
    "EXPIRED": "EXPIRED",
    "INACTIVE": "INACTIVE",
}


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
        raw[:10] + "T00:00:00" if len(raw) >= 10 else None,
    ]

    for cand in candidates:
        if cand is None:
            continue
        try:
            dt = datetime.fromisoformat(cand)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            continue
    return None


def calc_balance_cents(expire: datetime | None, now_utc: datetime) -> int:
    """  Конвертирует оставшиеся дни подписки в копейки для баланса."""
    if expire is None:
        return 0
    days_left = max(0, (expire - now_utc).days)
    return days_left * DAILY_COST_CENTS


def map_status(old_status: str | None) -> str:
    """  Маппит статус из старой схемы в новую. PAID -> ACTIVE."""
    return STATUS_MAP.get(old_status or "", "TRIAL")


async def migrate() -> None:
    sql_conn = sqlite3.connect(SQLITE_PATH)
    sql_conn.row_factory = sqlite3.Row
    pg_conn = await asyncpg.connect(PG_DSN)

    try:
        now_utc = datetime.now(timezone.utc)
        counts_before: dict[str, int] = {}
        counts_after: dict[str, int] = {}

        users = sql_conn.execute("SELECT * FROM users").fetchall()
        counts_before["users"] = len(users)
        counts_before["devices"] = sum(1 for u in users if u["uuid"] and u["vless_link"])
        counts_before["payments"] = 0

        async with pg_conn.transaction():

            # ----- users -----
            for u in users:
                expire = safe_parse_datetime(u["trial_expired_at"])
                balance_cents = calc_balance_cents(expire, now_utc)
                status = map_status(u["status"])

                await pg_conn.execute(
                    """
                    INSERT INTO users (
                        user_id, username, expire_at, vless_link, uuid,
                        status, notified, balance, last_billing_date, low_balance_notified
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NULL, FALSE)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    u["user_id"],
                    u["username"],
                    expire,
                    u["vless_link"],
                    u["uuid"],
                    status,
                    bool(u["notified"]),
                    balance_cents,
                )

            # ----- devices (1 устройство на юзера) -----
            for u in users:
                if not u["uuid"] or not u["vless_link"]:
                    continue

                await pg_conn.execute(
                    """
                    INSERT INTO devices (user_id, device_name, client_uuid, vless_link, monthly_cost)
                    VALUES ($1, 'Основное', $2, $3, $4)
                    ON CONFLICT (client_uuid) DO NOTHING
                    """,
                    u["user_id"],
                    u["uuid"],
                    u["vless_link"],
                    DAILY_COST_CENTS * 30,
                )

            # ----- balance_transactions — BONUS за оставшиеся дни -----
            for u in users:
                expire = safe_parse_datetime(u["trial_expired_at"])
                balance_cents = calc_balance_cents(expire, now_utc)
                if balance_cents <= 0:
                    continue

                await pg_conn.execute(
                    """
                    INSERT INTO balance_transactions (
                        user_id, kind, amount_cents,
                        balance_before, balance_after,
                        reference_type, reference_id, idempotency_key
                    )
                    VALUES ($1, 'BONUS', $2, 0, $2, 'migration', 'sqlite_import', $3)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """,
                    u["user_id"],
                    balance_cents,
                    f"migration:{u['user_id']}",
                )

        # ----- синхронизация sequences -----
        seq_name = await pg_conn.fetchval(
            "SELECT pg_get_serial_sequence($1, 'id')", "devices"
        )
        if seq_name:
            await pg_conn.execute(
                f"SELECT setval('{seq_name}', COALESCE((SELECT MAX(id) FROM devices), 1), true)"
            )

        # ----- сверка row counts -----
        for table in ("users", "devices", "payments"):
            counts_after[table] = await pg_conn.fetchval(f"SELECT COUNT(*) FROM {table}")

        # ----- вывод итогов -----
        has_mismatch = False
        print("=" * 60)
        print("Миграция завершена.")
        print(f"DAILY_COST_CENTS = {DAILY_COST_CENTS} ({DAILY_COST_CENTS / 100:.2f} руб/день)")
        print(f"{'  Таблица':<16} {'SQLite':>8} {'PostgreSQL':>12} {'Статус':>10}")
        print("-" * 60)

        for table in ("users", "devices", "payments"):
            before = counts_before.get(table, 0)
            after = counts_after.get(table, 0)
            ok = before == after
            status_str = "✓ OK" if ok else "✗ MISMATCH"
            if not ok:
                has_mismatch = True
            print(f"  {table:<14} {before:>8} {after:>12} {status_str:>10}")

        print("=" * 60)

        if has_mismatch:
            raise RuntimeError(
                f"Row count mismatch! {counts_before} -> {counts_after}"
            )

    finally:
        sql_conn.close()
        await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
