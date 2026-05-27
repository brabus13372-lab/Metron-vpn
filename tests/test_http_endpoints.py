import pytest
import httpx

import app.api as api
import app.deps as deps
from datetime import datetime, timezone


def _asgi_client():
    # NOTE: httpx versions differ on ASGITransport signature.
    # We avoid lifespan handling here and mock DB/panel calls in tests.
    transport = httpx.ASGITransport(app=api.app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_get_profile_requires_init_data(monkeypatch):
    monkeypatch.setattr(deps, "BOT_TOKEN", "token")

    async with _asgi_client() as client:
        res = await client.get("/api/user/1")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_get_profile_forbidden_on_user_mismatch(monkeypatch):
    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "extract_user_id", lambda *_a, **_k: 999)

    async with _asgi_client() as client:
        res = await client.get("/api/user/1", headers={"X-Telegram-Init-Data": "dummy"})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_get_profile_success(monkeypatch):
    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "extract_user_id", lambda *_a, **_k: 1)

    async def _fake_get_user_data_dict(_user_id: int):
        return {"user_id": 1, "status": "ACTIVE", "username": "alice", "expire_at": None, "vless_link": None}

    async def _fake_get_user_balance(_user_id: int):
        return 10000

    async def _fake_get_user_total_monthly_cost(_user_id: int):
        return 30000

    async def _fake_get_user_devices(_user_id: int):
        return []

    monkeypatch.setattr(api, "get_user_data_dict", _fake_get_user_data_dict)
    monkeypatch.setattr(api, "get_user_balance", _fake_get_user_balance)
    monkeypatch.setattr(api, "get_user_total_monthly_cost", _fake_get_user_total_monthly_cost)
    monkeypatch.setattr(api, "get_user_devices", _fake_get_user_devices)

    async with _asgi_client() as client:
        res = await client.get("/api/user/1", headers={"X-Telegram-Init-Data": "dummy"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["id"] == 1
    assert payload["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_create_device_blocks_when_limit_reached(monkeypatch):
    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "extract_user_id", lambda *_a, **_k: 1)

    async def _fake_get_user_data_dict(_user_id: int):
        return {"user_id": 1, "status": "ACTIVE", "username": "alice", "expire_at": None, "vless_link": None}

    async def _fake_get_user_balance(_user_id: int):
        return 10000

    async def _fake_get_user_devices(_user_id: int):
        return [{"id": i, "is_active": True, "monthly_cost": 100, "device_name": f"d{i}"} for i in range(1, 6)]

    monkeypatch.setattr(api, "get_user_data_dict", _fake_get_user_data_dict)
    monkeypatch.setattr(api, "get_user_balance", _fake_get_user_balance)
    monkeypatch.setattr(api, "get_user_devices", _fake_get_user_devices)

    async with _asgi_client() as client:
        res = await client.post(
            "/api/user/1/devices",
            headers={"X-Telegram-Init-Data": "dummy"},
            json={"device_name": "new"},
        )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_rotate_key_blocks_when_user_has_devices(monkeypatch):
    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "extract_user_id", lambda *_a, **_k: 1)

    async def _fake_get_user_data_dict(_user_id: int):
        return {"user_id": 1, "status": "TRIAL", "username": "alice", "uuid": None, "vless_link": None}

    async def _fake_get_user_devices(_user_id: int):
        return [{"id": 1, "is_active": True, "monthly_cost": 100, "device_name": "d1"}]

    monkeypatch.setattr(api, "get_user_data_dict", _fake_get_user_data_dict)
    monkeypatch.setattr(api, "get_user_devices", _fake_get_user_devices)

    async with _asgi_client() as client:
        res = await client.post("/api/user/1/rotate-key", headers={"X-Telegram-Init-Data": "dummy"})
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_support_rate_limit_is_enforced_http(monkeypatch):
    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "extract_user_id", lambda *_a, **_k: 1)
    api._support_ticket_last_sent.clear()
    monkeypatch.setattr(api, "ADMIN_ID", 0)

    async def _fake_get_user_data_dict(_user_id: int):
        return {"user_id": 1, "status": "ACTIVE", "username": "alice"}

    async def _fake_create_ticket(*, user_id: int, message: str, files):
        return 123

    async def _fake_get_user_tickets(_user_id: int, limit: int = 1):
        return [{
            "id": 123,
            "message": "hello support",
            "status": "pending",
            "files": [],
            "created_at": datetime.now(timezone.utc),
            "answered_at": None,
        }]

    monkeypatch.setattr(api, "get_user_data_dict", _fake_get_user_data_dict)
    monkeypatch.setattr(api, "create_ticket", _fake_create_ticket)
    monkeypatch.setattr(api, "get_user_tickets", _fake_get_user_tickets)

    async with _asgi_client() as client:
        res1 = await client.post(
            "/api/user/1/support",
            headers={"X-Telegram-Init-Data": "dummy"},
            data={"message": "hello support"},
        )
        res2 = await client.post(
            "/api/user/1/support",
            headers={"X-Telegram-Init-Data": "dummy"},
            data={"message": "hello support"},
        )

    assert res1.status_code == 201
    assert res2.status_code == 429
