import sqlite3
import shutil
import logging
from pathlib import Path

DB_PATH = "metron.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def column_exists(conn, table: str, column: str) -> bool:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(c[1] == column for c in cols)


def table_exists(conn, table: str) -> bool:
    """Проверяет, существует ли таблица"""
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone()
    return result is not None


def apply_migrations(db_path: str = DB_PATH) -> None:
    target = Path(db_path)

    if not target.exists():
        logger.info("БД не найдена — пропуск.")
        return

    backup = target.with_suffix(".db.bak")
    shutil.copy2(target, backup)
    logger.info("Создан бэкап: %s", backup)

    conn = sqlite3.connect(target)

    try:
        conn.execute("PRAGMA locking_mode = EXCLUSIVE")
        conn.execute("BEGIN EXCLUSIVE")

        # =========================
        # VERSION TABLE
        # =========================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()[0]

        # =========================
        # V1 — копейки (у тебя уже есть)
        # =========================
        if version < 1:
            logger.info("Применяем миграцию v1 (копейки)")

            conn.executescript("""
                UPDATE users 
                SET balance = CAST(ROUND(COALESCE(balance, 0) * 100) AS INTEGER);

                UPDATE devices 
                SET monthly_cost = CAST(ROUND(COALESCE(monthly_cost, 0) * 100) AS INTEGER);
            """)

            conn.execute("INSERT INTO schema_version (version) VALUES (1)")
            version = 1

        # ... (всё что было до v2 остаётся)

        # =========================
        # V2 — защита платежей
        # =========================
        if version < 2:
            logger.info("Применяем миграцию v2 (payments safety)")

            if not column_exists(conn, "users", "last_payment_payload"):
                conn.execute("""
                    ALTER TABLE users ADD COLUMN last_payment_payload TEXT
                """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    payload TEXT,
                    telegram_charge_id TEXT,
                    provider_charge_id TEXT,
                    amount INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_payments_user 
                ON payments(user_id)
            """)

            conn.execute("INSERT INTO schema_version (version) VALUES (2)")
            version = 2

                # =========================
        # V3 — UNIQUE для provider_charge_id
        # =========================
        if version < 3:
            logger.info("Применяем миграцию v3 (payments UNIQUE constraint)")

            if not table_exists(conn, "payments"):
                logger.info("Таблица payments отсутствует, создаём сразу корректную схему")

                conn.execute("""
                    CREATE TABLE payments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        payload TEXT,
                        telegram_charge_id TEXT,
                        provider_charge_id TEXT UNIQUE NOT NULL,
                        amount INTEGER NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(user_id) REFERENCES users(user_id)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_payments_user
                    ON payments(user_id)
                """)
            else:
                row = conn.execute("""
                    SELECT sql
                    FROM sqlite_master
                    WHERE type='table' AND name='payments'
                """).fetchone()

                create_sql = row[0] if row else ""
                has_unique = "provider_charge_id TEXT UNIQUE" in create_sql or \
                             "provider_charge_id TEXT UNIQUE NOT NULL" in create_sql

                if has_unique:
                    logger.info("UNIQUE на provider_charge_id уже существует, пропускаем пересоздание")
                else:
                    logger.info("Пересоздаём payments с UNIQUE(provider_charge_id)")

                    conn.execute("DROP TABLE IF EXISTS payments_old")
                    conn.execute("ALTER TABLE payments RENAME TO payments_old")

                    conn.execute("""
                        CREATE TABLE payments (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER NOT NULL,
                            payload TEXT,
                            telegram_charge_id TEXT,
                            provider_charge_id TEXT UNIQUE NOT NULL,
                            amount INTEGER NOT NULL,
                            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY(user_id) REFERENCES users(user_id)
                        )
                    """)

                    conn.execute("""
                        INSERT OR IGNORE INTO payments (
                            id,
                            user_id,
                            payload,
                            telegram_charge_id,
                            provider_charge_id,
                            amount,
                            created_at
                        )
                        SELECT
                            id,
                            user_id,
                            payload,
                            telegram_charge_id,
                            provider_charge_id,
                            amount,
                            created_at
                        FROM payments_old
                        WHERE provider_charge_id IS NOT NULL
                          AND TRIM(provider_charge_id) != ''
                        ORDER BY id
                    """)

                    conn.execute("DROP TABLE payments_old")

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_payments_user
                        ON payments(user_id)
                    """)

                    logger.info("✅ Таблица payments пересоздана с UNIQUE(provider_charge_id)")

            conn.execute("INSERT INTO schema_version (version) VALUES (3)")
            version = 3

        logger.info("Миграции завершены. Текущая версия: %s", version)

        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()

        shutil.copy2(backup, target)
        logger.error("ОТКАТ К БЭКАПУ! Ошибка: %s", e)
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    apply_migrations()