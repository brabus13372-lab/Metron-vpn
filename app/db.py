import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any, List, Iterator

DB_PATH = "metron.db"
DB_TIMEOUT = 30.0

if sqlite3.sqlite_version_info < (3, 35, 0):
    raise RuntimeError(f"SQLite 3.35.0+ required for RETURNING clause. Current: {sqlite3.sqlite_version}")

@contextmanager
def get_db_connection(commit: bool = False) -> Iterator[sqlite3.Connection]:
    """Управляет соединением с БД. Автоматически коммитит/откатывает транзакции."""
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db() -> None:
    """Инициализирует схему БД."""
    with get_db_connection(commit=True) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, 
                username TEXT,
                trial_expired_at TEXT, 
                vless_link TEXT,
                uuid TEXT UNIQUE, 
                status TEXT DEFAULT 'NEW', 
                notified INTEGER DEFAULT 0,
                balance INTEGER DEFAULT 0,
                last_billing_date TEXT
            );
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                device_name TEXT NOT NULL,
                client_uuid TEXT UNIQUE NOT NULL,
                vless_link TEXT,
                monthly_cost INTEGER DEFAULT 10000,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
                           
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                telegram_charge_id TEXT NOT NULL,
                provider_charge_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_tg_charge_id
            ON payments(telegram_charge_id);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_provider_charge_id
            ON payments(provider_charge_id);

            CREATE INDEX IF NOT EXISTS idx_payments_user_id
            ON payments(user_id);
                           
            CREATE INDEX IF NOT EXISTS idx_devices_user_id ON devices(user_id);
            CREATE INDEX IF NOT EXISTS idx_users_uuid ON users(uuid);
        ''')

def add_balance_atomic(user_id: int, amount_cents: int) -> Optional[Decimal]:
    """Атомарно пополняет баланс. Возвращает новый баланс в рублях."""
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")

    with get_db_connection(commit=True) as conn:
        row = conn.execute("""
            UPDATE users 
            SET balance = balance + ? 
            WHERE user_id = ? 
            RETURNING balance
        """, (amount_cents, user_id)).fetchone()

        if not row:
            return None

        new_balance_cents: int = row["balance"]
        return Decimal(new_balance_cents) / 100
    
def charge_balance_atomic(user_id: int, amount_cents: int) -> Optional[Decimal]:
    """
    Атомарно списывает amount_cents с баланса пользователя.
    Возвращает новый баланс в рублях или None, если пользователь не найден.
    """
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")

    with get_db_connection(commit=True) as conn:
        row = conn.execute("""
            UPDATE users
            SET balance = balance - ?
            WHERE user_id = ?
            RETURNING balance
        """, (amount_cents, user_id)).fetchone()

        if not row:
            return None

        return Decimal(row["balance"]) / 100
    
def charge_daily_billing_atomic(user_id: int, amount_cents: int, billing_ts: str) -> Optional[Dict[str, Any]]:
    """
    Атомарно списывает ежедневный платёж и обновляет last_billing_date,
    только если за текущий день списание ещё не выполнялось
    и если на балансе достаточно средств.

    Возвращает:
    - dict с результатом, если пользователь найден:
        {
            "charged": bool,
            "balance_before": Decimal,
            "balance_after": Decimal,
            "already_charged_today": bool,
            "insufficient_funds": bool,
        }
    - None, если пользователь не найден
    """
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")

    billing_day = billing_ts[:10]

    with get_db_connection(commit=True) as conn:
        row = conn.execute("""
            SELECT balance, last_billing_date
            FROM users
            WHERE user_id = ?
        """, (user_id,)).fetchone()

        if not row:
            return None

        balance_before_cents = int(row["balance"])
        last_billing_date = row["last_billing_date"]

        if last_billing_date and str(last_billing_date).startswith(billing_day):
            return {
                "charged": False,
                "balance_before": Decimal(balance_before_cents) / 100,
                "balance_after": Decimal(balance_before_cents) / 100,
                "already_charged_today": True,
                "insufficient_funds": False,
            }

        if balance_before_cents < amount_cents:
            return {
                "charged": False,
                "balance_before": Decimal(balance_before_cents) / 100,
                "balance_after": Decimal(balance_before_cents) / 100,
                "already_charged_today": False,
                "insufficient_funds": True,
            }

        updated = conn.execute("""
            UPDATE users
            SET balance = balance - ?,
                last_billing_date = ?
            WHERE user_id = ?
              AND balance >= ?
            RETURNING balance
        """, (amount_cents, billing_ts, user_id, amount_cents)).fetchone()

        if not updated:
            return {
                "charged": False,
                "balance_before": Decimal(balance_before_cents) / 100,
                "balance_after": Decimal(balance_before_cents) / 100,
                "already_charged_today": False,
                "insufficient_funds": True,
            }

        balance_after_cents = int(updated["balance"])

        return {
            "charged": True,
            "balance_before": Decimal(balance_before_cents) / 100,
            "balance_after": Decimal(balance_after_cents) / 100,
            "already_charged_today": False,
            "insufficient_funds": False,
        }
    
def get_user_balance(user_id: int) -> Optional[Decimal]:
    """Возвращает баланс в рублях или None, если пользователь не найден."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return Decimal(row["balance"]) / 100 if row else None

def set_user_balance(user_id: int, amount_cents: int) -> None:
    """Принудительно устанавливает баланс в копейках."""
    with get_db_connection(commit=True) as conn:
        conn.execute("UPDATE users SET balance = ? WHERE user_id = ?", (amount_cents, user_id))

def save_user(
    user_id: int,
    username: str,
    expire_at: str,
    vless_link: Optional[str],
    uuid_val: Optional[str],
    status: str = "TRIAL",
) -> None:
    """Создаёт или обновляет пользователя (UPSERT), не затрагивая баланс."""
    clean_vless_link = vless_link or None
    clean_uuid = uuid_val or None

    with get_db_connection(commit=True) as conn:
        conn.execute("""
            INSERT INTO users (
                user_id,
                username,
                trial_expired_at,
                vless_link,
                uuid,
                status,
                notified
            )
            VALUES (?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                trial_expired_at = excluded.trial_expired_at,
                vless_link = excluded.vless_link,
                uuid = excluded.uuid,
                status = excluded.status,
                notified = 0
        """, (
            user_id,
            username,
            expire_at,
            clean_vless_link,
            clean_uuid,
            status,
        ))

def get_user_data_dict(user_id: int) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        row = conn.execute("""
            SELECT
                trial_expired_at AS expire_at,
                vless_link,
                uuid,
                username,
                status,
                balance
            FROM users
            WHERE user_id = ?
        """, (user_id,)).fetchone()
        return dict(row) if row else None

def update_user_link(user_id: int, new_link: str, uuid_val: Optional[str] = None) -> None:
    """Обновляет ссылку пользователя и опционально его UUID."""
    with get_db_connection(commit=True) as conn:
        if uuid_val:
            conn.execute("UPDATE users SET vless_link = ?, uuid = ? WHERE user_id = ?", (new_link, uuid_val, user_id))
        else:
            conn.execute("UPDATE users SET vless_link = ? WHERE user_id = ?", (new_link, user_id))

def add_device(user_id: int, device_name: str, client_uuid: str, vless_link: str, monthly_cost_cents: int = 10000) -> int:
    """Добавляет устройство пользователю и возвращает его ID."""
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection(commit=True) as conn:
        return conn.execute("""
            INSERT INTO devices (user_id, device_name, client_uuid, vless_link, monthly_cost, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, device_name, client_uuid, vless_link, monthly_cost_cents, created_at)).lastrowid

def remove_device(device_id: int, user_id: Optional[int] = None) -> int:
    """Удаляет устройство. Возвращает количество удаленных строк."""
    with get_db_connection(commit=True) as conn:
        if user_id:
            return conn.execute("DELETE FROM devices WHERE id = ? AND user_id = ?", (device_id, user_id)).rowcount
        return conn.execute("DELETE FROM devices WHERE id = ?", (device_id,)).rowcount

def get_user_devices(user_id: int) -> List[Dict[str, Any]]:
    """Возвращает список устройств. Стоимость (monthly_cost) конвертируется в рубли (Decimal)."""
    with get_db_connection() as conn:
        rows = conn.execute("""
            SELECT id, device_name, client_uuid, vless_link, monthly_cost, created_at
            FROM devices WHERE user_id = ? ORDER BY created_at DESC
        """, (user_id,)).fetchall()
        return [{**dict(row), "monthly_cost": Decimal(row["monthly_cost"]) / 100} for row in rows]
    
def get_all_users_with_devices() -> List[int]:
    """Возвращает список user_id, у которых есть хотя бы одно устройство."""
    with get_db_connection() as conn:
        rows = conn.execute("""
            SELECT DISTINCT user_id
            FROM devices
            ORDER BY user_id
        """).fetchall()
        return [row["user_id"] for row in rows]

def get_user_total_monthly_cost(user_id: int) -> Decimal:
    """Возвращает суммарную стоимость всех устройств пользователя в рублях."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT COALESCE(SUM(monthly_cost), 0) AS total FROM devices WHERE user_id = ?", (user_id,)).fetchone()
        return Decimal(row["total"]) / 100

def update_user_last_billing_date(user_id: int, date_str: str) -> None:
    """Обновляет дату последнего списания средств."""
    with get_db_connection(commit=True) as conn:
        conn.execute("UPDATE users SET last_billing_date = ? WHERE user_id = ?", (date_str, user_id))

def get_user_last_billing_date(user_id: int) -> Optional[str]:
    """Возвращает дату последнего списания или None."""
    with get_db_connection() as conn:
        row = conn.execute("""
            SELECT last_billing_date
            FROM users
            WHERE user_id = ?
        """, (user_id,)).fetchone()
        return row["last_billing_date"] if row and row["last_billing_date"] else None

def deactivate_panel_client(client_uuid: str) -> None:
    """Деактивирует пользователя по UUID клиента."""
    with get_db_connection(commit=True) as conn:
        conn.execute("UPDATE users SET status = 'INACTIVE' WHERE uuid = ?", (client_uuid,))