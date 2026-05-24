#!/usr/bin/env python3
"""
cleanup_orphan_keys.py
──────────────────────
Одноразовый миграционный скрипт.

Проблема:
    До того как в /rotate-key появился гард, пользователи могли получить
    аккаунтный ключ (users.uuid / users.vless_link) И отдельные устройства
    в таблице devices.  В итоге в панели был «лишний» клиент (account-level),
    который никогда не биллировался — дыра ~100₽/мес на пользователя.

Что делает скрипт:
    1. Находит всех пользователей у которых:
         - users.uuid IS NOT NULL   (есть аккаунтный клиент в панели)
         - есть хотя бы 1 активное устройство в таблице devices
    2. Для каждого такого пользователя:
         a. Удаляет клиента из 3x-ui панели по users.uuid
         b. Обнуляет users.vless_link и users.uuid в БД

Режимы запуска:
    --dry-run   (по умолчанию) — только показывает что будет сделано
    --apply     — реально чистит панель и БД

Переменные окружения:
    DATABASE_URL  — asyncpg DSN
    PANEL_URL     — https://your-panel.com:port
    PANEL_USER    — логин в панели
    PANEL_PASS    — пароль в панели
    INBOUND_ID    — id инбаунда (integer, default=1)

Использование:
    export $(grep -v '^#' .env | xargs) && python cleanup_orphan_keys.py --dry-run
    export $(grep -v '^#' .env | xargs) && python cleanup_orphan_keys.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import asyncpg
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("cleanup")

# ─── env ──────────────────────────────────────────────────────────────────────

PANEL_URL  = os.environ.get("PANEL_URL", "").rstrip("/")
PANEL_USER = os.environ.get("PANEL_USER", "")
PANEL_PASS = os.environ.get("PANEL_PASS", "")
INBOUND_ID = int(os.environ.get("INBOUND_ID", "1"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

_session_cookie: str | None = None


# ─── panel helpers ────────────────────────────────────────────────────────────

async def _panel_login(client: httpx.AsyncClient) -> bool:
    global _session_cookie
    try:
        resp = await client.post(
            f"{PANEL_URL}/login",
            data={"username": PANEL_USER, "password": PANEL_PASS},
            follow_redirects=True,
            timeout=15,
        )
    except Exception as exc:
        log.error("panel: login request failed: %s", exc)
        return False

    try:
        body = resp.json()
    except Exception:
        log.error("panel: login non-JSON response status=%s", resp.status_code)
        return False

    if resp.status_code == 200 and body.get("success"):
        _session_cookie = "; ".join(f"{k}={v}" for k, v in resp.cookies.items())
        log.info("panel: login OK")
        return True

    log.error("panel: login FAILED status=%s body=%s", resp.status_code, str(body)[:200])
    return False


async def _panel_delete_client(
    client: httpx.AsyncClient,
    client_uuid: str,
) -> tuple[bool, str]:
    """
    POST /panel/api/inbounds/{INBOUND_ID}/delClient/{uuid}
    Returns (success, message).
    «not found» считается успехом — клиента уже нет.
    """
    headers = {"Cookie": _session_cookie or ""}
    try:
        resp = await client.post(
            f"{PANEL_URL}/panel/api/inbounds/{INBOUND_ID}/delClient/{client_uuid}",
            headers=headers,
            timeout=15,
        )
        body = resp.json()
    except Exception as exc:
        return False, str(exc)

    success = body.get("success", False)
    msg     = body.get("msg", "")

    if not success and "not found" in str(msg).lower():
        return True, "already_absent"

    return success, msg


# ─── DB helpers ───────────────────────────────────────────────────────────────

async def find_orphans(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT
            u.user_id,
            u.username,
            u.uuid           AS account_uuid,
            u.vless_link     AS account_link,
            COUNT(d.id)      AS active_device_count
        FROM users u
        JOIN devices d
             ON d.user_id  = u.user_id
            AND d.is_active = TRUE
        WHERE u.uuid IS NOT NULL
        GROUP BY u.user_id, u.username, u.uuid, u.vless_link
        ORDER BY u.user_id
        """
    )
    return [dict(r) for r in rows]


async def clear_account_key_in_db(pool: asyncpg.Pool, user_id: int) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE users SET vless_link = NULL, uuid = NULL WHERE user_id = $1",
                user_id,
            )


# ─── main ─────────────────────────────────────────────────────────────────────

async def run(apply: bool) -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL not set"); sys.exit(1)
    if apply and not PANEL_URL:
        log.error("PANEL_URL not set"); sys.exit(1)
    if apply and not PANEL_USER:
        log.error("PANEL_USER not set"); sys.exit(1)
    if apply and not PANEL_PASS:
        log.error("PANEL_PASS not set"); sys.exit(1)

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    orphans = await find_orphans(pool)

    if not orphans:
        log.info("✅  No orphan account-level keys found — nothing to do.")
        await pool.close()
        return

    log.info("Found %d user(s) with orphan account-level panel client(s):", len(orphans))
    for o in orphans:
        log.info(
            "  user_id=%-10s  username=%-20s  account_uuid=%s  active_devices=%s",
            o["user_id"], o["username"] or "—",
            o["account_uuid"], o["active_device_count"],
        )

    if not apply:
        log.info("")
        log.info("DRY-RUN mode — nothing changed. Re-run with --apply to proceed.")
        await pool.close()
        return

    # ── APPLY ──────────────────────────────────────────────────────────────
    log.info("")
    log.info("APPLY mode — starting cleanup...")

    async with httpx.AsyncClient(verify=False) as http:
        if not await _panel_login(http):
            log.error("Cannot proceed without panel auth."); sys.exit(1)

        ok_count = fail_count = 0

        for o in orphans:
            uid   = o["user_id"]
            auuid = o["account_uuid"]
            log.info("Processing user_id=%s  uuid=%s ...", uid, auuid)

            panel_ok, panel_msg = await _panel_delete_client(http, auuid)

            if panel_ok:
                log.info("  ✓ panel: client removed (msg=%s)", panel_msg or "ok")
                await clear_account_key_in_db(pool, uid)
                log.info("  ✓ db:    users.vless_link / uuid → NULL")
                ok_count += 1
            else:
                log.warning(
                    "  ✗ panel: delete FAILED  user_id=%s  uuid=%s  msg=%s"
                    " — DB NOT touched, will retry on next run",
                    uid, auuid, panel_msg,
                )
                fail_count += 1

    log.info("")
    log.info("Done.  ok=%d  failed=%d", ok_count, fail_count)
    if fail_count:
        log.warning(
            "%d client(s) could not be removed from the panel. "
            "Check the panel manually, then re-run --apply.",
            fail_count,
        )

    await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove orphan account-level VPN keys left before the guard was added"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="Only show what would be done (default)",
    )
    mode.add_argument(
        "--apply", dest="dry_run", action="store_false",
        help="Actually delete panel clients and clear DB fields",
    )
    args = parser.parse_args()
    asyncio.run(run(apply=not args.dry_run))


if __name__ == "__main__":
    main()
