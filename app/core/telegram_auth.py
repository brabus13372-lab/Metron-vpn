"""
Проверка подлинности Telegram WebApp initData (HMAC-SHA256).

https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any
from urllib.parse import parse_qsl, unquote

logger = logging.getLogger(__name__)


class TelegramInitDataError(Exception):
    """initData не прошла проверку."""


def _data_check_string(parsed: dict[str, str]) -> str:
    pairs = sorted(f"{k}={v}" for k, v in parsed.items() if k != "hash")
    return "\n".join(pairs)


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_sec: int = 86400,
) -> dict[str, Any]:
    """
  Возвращает распарсенные поля initData; ключ ``user`` — dict с полем ``id``.
    """
    raw = (init_data or "").strip()
    if not raw:
        raise TelegramInitDataError("empty init data")

    parsed = dict(parse_qsl(raw, keep_blank_values=True))
    received_hash = parsed.get("hash")
    if not received_hash:
        raise TelegramInitDataError("hash missing")

    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    calculated = hmac.new(
        secret_key,
        _data_check_string(parsed).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise TelegramInitDataError("invalid signature")

    auth_date_raw = parsed.get("auth_date")
    if not auth_date_raw:
        raise TelegramInitDataError("auth_date missing")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise TelegramInitDataError("invalid auth_date") from exc

    now = int(time.time())
    if max_age_sec > 0 and now - auth_date > max_age_sec:
        raise TelegramInitDataError("init data expired")

    user_raw = parsed.get("user")
    if not user_raw:
        raise TelegramInitDataError("user missing")

    try:
        user = json.loads(unquote(user_raw) if "%" in user_raw else user_raw)
    except json.JSONDecodeError as exc:
        raise TelegramInitDataError("invalid user json") from exc

    if not isinstance(user, dict) or "id" not in user:
        raise TelegramInitDataError("user id missing")

    try:
        user_id = int(user["id"])
    except (TypeError, ValueError) as exc:
        raise TelegramInitDataError("invalid user id") from exc

    return {
        "user_id": user_id,
        "user": user,
        "auth_date": auth_date,
        "parsed": parsed,
    }


def extract_user_id(init_data: str, bot_token: str, *, max_age_sec: int = 86400) -> int:
    return validate_init_data(init_data, bot_token, max_age_sec=max_age_sec)["user_id"]
