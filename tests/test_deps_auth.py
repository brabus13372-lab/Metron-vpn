from fastapi import HTTPException
from starlette.requests import Request

import pytest

import app.deps as deps


def _request_with_headers(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/user/1",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_verify_telegram_user_access_allows_matching_user(monkeypatch):
    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "WEBAPP_INIT_MAX_AGE_SEC", 3600)
    monkeypatch.setattr(deps, "extract_user_id", lambda *_args, **_kwargs: 1001)
    request = _request_with_headers({"x-telegram-init-data": "dummy"})

    result = await deps.verify_telegram_user_access(
        user_id=1001,
        request=request,
        x_telegram_init_data="dummy",
    )
    assert result == 1001


@pytest.mark.asyncio
async def test_verify_telegram_user_access_rejects_mismatch(monkeypatch):
    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "extract_user_id", lambda *_args, **_kwargs: 999)
    request = _request_with_headers({"x-telegram-init-data": "dummy"})

    with pytest.raises(HTTPException) as exc:
        await deps.verify_telegram_user_access(
            user_id=1001,
            request=request,
            x_telegram_init_data="dummy",
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_verify_telegram_user_access_accepts_authorization_tma(monkeypatch):
    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "extract_user_id", lambda *_args, **_kwargs: 7)
    request = _request_with_headers({"authorization": "tma init-data-via-auth-header"})

    result = await deps.verify_telegram_user_access(
        user_id=7,
        request=request,
        x_telegram_init_data=None,
    )
    assert result == 7
