"""FastAPI dependencies."""

from __future__ import annotations

import logging

from fastapi import Header, HTTPException, Request

from app.config import BOT_TOKEN, WEBAPP_INIT_MAX_AGE_SEC
from app.core.telegram_auth import TelegramInitDataError, extract_user_id

logger = logging.getLogger(__name__)

_INIT_DATA_HEADER = "x-telegram-init-data"


def _read_init_data(request: Request, header_value: str | None) -> str:
    if header_value and header_value.strip():
        return header_value.strip()

    auth = request.headers.get("authorization") or ""
    prefix = "tma "
    if auth.lower().startswith(prefix):
        return auth[len(prefix) :].strip()

    return ""


async def verify_telegram_user_access(
    user_id: int,
    request: Request,
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> int:
    """
    Проверяет HMAC initData и совпадение Telegram user id с ``user_id`` в пути.
    """
    if not BOT_TOKEN:
        logger.error("verify_telegram_user_access: BOT_TOKEN not configured")
        raise HTTPException(status_code=503, detail="Auth not configured")

    init_data = _read_init_data(request, x_telegram_init_data)
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication required")

    try:
        tg_user_id = extract_user_id(
            init_data,
            BOT_TOKEN,
            max_age_sec=WEBAPP_INIT_MAX_AGE_SEC,
        )
    except TelegramInitDataError as exc:
        logger.warning("init_data_rejected user_id=%s reason=%s", user_id, exc)
        raise HTTPException(status_code=401, detail="Invalid Telegram authentication") from exc

    if tg_user_id != user_id:
        logger.warning(
            "init_data_user_mismatch path_user=%s init_user=%s",
            user_id,
            tg_user_id,
        )
        raise HTTPException(status_code=403, detail="Forbidden")

    return user_id
