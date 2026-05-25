"""
Database: asyncpg connection pool + shared helpers.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import AsyncIterator, Optional

import asyncpg

logger = logging.getLogger(__name__)


class Database:
    """Тонкая обёртка над asyncpg Pool."""

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10) -> None:
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None
        self._min_size = min_size
        self._max_size = max_size

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            command_timeout=10.0,
        )
        logger.info("DB pool created (min=%s, max=%s)", self._min_size, self._max_size)

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("DB pool closed")

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[asyncpg.Connection]:
        if not self._pool:
            raise RuntimeError("Pool not initialized")
        async with self._pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        if not self._pool:
            raise RuntimeError("Pool not initialized")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    async def verify_schema(self) -> None:
        if not self._pool:
            raise RuntimeError("Pool not initialized")

        required_tables = (
            "users",
            "devices",
            "payments",
            "support_tickets",
            "balance_transactions",
        )
        missing_tables: list[str] = []

        async with self.connection() as conn:
            for table_name in required_tables:
                exists = await conn.fetchval("SELECT to_regclass($1)", table_name)
                if exists is None:
                    missing_tables.append(table_name)

        if missing_tables:
            missing = ", ".join(missing_tables)
            raise RuntimeError(
                "Database schema is not initialized. Missing tables: "
                f"{missing}. Run `alembic upgrade head` before starting the app."
            )

    @staticmethod
    def _cents_to_rubles(cents: int) -> Decimal:
        return Decimal(cents) / 100


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_db: Optional[Database] = None


async def init_db() -> Database:
    global _db
    if _db is not None:
        return _db
    dsn = os.environ["DATABASE_URL"]
    db = Database(dsn)
    await db.connect()
    try:
        await db.verify_schema()
    except Exception:
        await db.disconnect()
        raise
    _db = db
    return db


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.disconnect()
        _db = None


def get_db() -> Database:
    if _db is None:
        raise RuntimeError("Database is not initialized. Call init_db() first.")
    return _db
