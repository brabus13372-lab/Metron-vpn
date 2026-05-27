import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.core.telegram_auth import TelegramInitDataError, extract_user_id, validate_init_data


def _build_init_data(bot_token: str, user_id: int, auth_date: int | None = None) -> str:
    auth_date = auth_date or int(time.time())
    user_payload = json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":"))
    parsed = {
        "auth_date": str(auth_date),
        "query_id": "AAEAAAE",
        "user": user_payload,
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    parsed["hash"] = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(parsed)


def test_validate_init_data_success():
    token = "123456:abc-token"
    raw = _build_init_data(token, user_id=777001)
    result = validate_init_data(raw, token, max_age_sec=3600)
    assert result["user_id"] == 777001
    assert result["auth_date"] > 0


def test_extract_user_id_expired():
    token = "123456:abc-token"
    old_auth_date = int(time.time()) - 1000
    raw = _build_init_data(token, user_id=42, auth_date=old_auth_date)
    with pytest.raises(TelegramInitDataError, match="expired"):
        extract_user_id(raw, token, max_age_sec=10)


def test_validate_init_data_invalid_signature():
    token = "123456:abc-token"
    raw = _build_init_data(token, user_id=42)
    tampered = raw.replace("query_id=AAEAAAE", "query_id=HACKED")
    with pytest.raises(TelegramInitDataError, match="invalid signature"):
        validate_init_data(tampered, token)
