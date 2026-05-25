"""
Alembic env.py — синхронный runner поверх PostgreSQL.

Подключается через DATABASE_URL из окружения.
Формат: postgresql+psycopg2://user:pass@host/dbname
  (asyncpg DSN: замени 'postgresql://' или 'postgresql+asyncpg://' авто-ниже)

Зависимость: psycopg2-binary (только для миграций, не для рантайма бота).
"""
from __future__ import annotations

import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Читаем конфиг логгинга из alembic.ini
# ---------------------------------------------------------------------------
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# DB URL: берём из DATABASE_URL и нормализуем драйвер на psycopg2
# ---------------------------------------------------------------------------
_raw_url = os.environ["DATABASE_URL"]  # упадёт явно, если не задан

# asyncpg DSN → psycopg2 DSN
_url = re.sub(
    r"^postgresql(?:\+asyncpg)?://",
    "postgresql+psycopg2://",
    _raw_url,
)
config.set_main_option("sqlalchemy.url", _url)

# Метаданные не используем (raw SQL миграции)
target_metadata = None


# ---------------------------------------------------------------------------
# Offline mode (генерация SQL без подключения)
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode (применение к живой БД)
# ---------------------------------------------------------------------------
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # одно соединение на вызов, нет утечек
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
