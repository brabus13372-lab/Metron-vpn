from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import app.api as api
from app.schemas import DeviceCreateIn


@pytest.mark.asyncio
async def test_create_device_enforces_max_devices(monkeypatch):
    async def _fake_get_user_or_404(_user_id: int):
        return {"status": "ACTIVE", "username": "alice"}

    async def _fake_get_user_balance(_user_id: int):
        return 500

    async def _fake_get_user_devices(_user_id: int):
        return [
            {"id": i, "is_active": True, "monthly_cost": 100, "device_name": f"d{i}"}
            for i in range(1, 6)
        ]

    async def _fake_raise_user_action_error(**kwargs):
        raise HTTPException(status_code=kwargs["status_code"], detail=kwargs["detail"])

    monkeypatch.setattr(api, "_get_user_or_404", _fake_get_user_or_404)
    monkeypatch.setattr(api, "get_user_balance", _fake_get_user_balance)
    monkeypatch.setattr(api, "get_user_devices", _fake_get_user_devices)
    monkeypatch.setattr(api, "_raise_user_action_error", _fake_raise_user_action_error)

    with pytest.raises(HTTPException) as exc:
        await api.create_device(1, DeviceCreateIn(device_name="new-device"))

    assert exc.value.status_code == 409
    assert "Device limit reached" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_support_ticket_rate_limit(monkeypatch):
    api._support_ticket_last_sent.clear()
    monkeypatch.setattr(api, "ADMIN_ID", 0)

    async def _fake_get_user_or_404(_user_id: int):
        return {"user_id": 1, "username": "alice"}

    async def _fake_create_ticket(*, user_id: int, message: str, files):
        assert user_id == 1
        assert message == "hello support"
        assert files == []
        return 10

    async def _fake_get_user_tickets(_user_id: int, limit: int = 1):
        return [{
            "id": 10,
            "message": "hello support",
            "status": "pending",
            "files": [],
            "created_at": datetime.now(timezone.utc),
            "answered_at": None,
        }]

    monkeypatch.setattr(api, "_get_user_or_404", _fake_get_user_or_404)
    monkeypatch.setattr(api, "create_ticket", _fake_create_ticket)
    monkeypatch.setattr(api, "get_user_tickets", _fake_get_user_tickets)

    first = await api.submit_support_ticket(user_id=1, message="hello support", files=None)
    assert first.id == 10

    with pytest.raises(HTTPException) as exc:
        await api.submit_support_ticket(user_id=1, message="hello support", files=None)

    assert exc.value.status_code == 429
