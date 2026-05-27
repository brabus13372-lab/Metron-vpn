#!/usr/bin/env python3
"""Генерация валидного initData для ручной проверки API (локально)."""
import hashlib
import hmac
import json
import sys
import time
from urllib.parse import quote, urlencode

from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

import sys

sys.path.insert(0, str(ROOT))

from app.config import BOT_TOKEN  # noqa: E402


def build_init_data(user_id: int, username: str = "tester") -> str:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN not set")

    user_json = json.dumps(
        {"id": user_id, "first_name": "Test", "username": username},
        separators=(",", ":"),
    )
    payload = {
        "auth_date": str(int(time.time())),
        "user": user_json,
    }
    pairs = sorted(f"{k}={v}" for k, v in payload.items())
    data_check_string = "\n".join(pairs)
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    payload["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(payload, quote_via=quote)


if __name__ == "__main__":
    uid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if uid <= 0:
        raise SystemExit("usage: test_telegram_auth.py <telegram_user_id>")
    print(build_init_data(uid))
