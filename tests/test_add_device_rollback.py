import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock

import app.api as api
from app.bot.handlers import profile as profile_module
from app.schemas import DeviceCreateIn
from app.services import vpn as vpn_module


@pytest.mark.asyncio
async def test_api_create_device_rollbacks_panel_on_db_fail(monkeypatch):
    client_uuid = "11111111-2222-3333-4444-555555555555"

    async def _fake_get_user_or_404(_user_id: int):
        return {"status": "ACTIVE", "username": "alice"}

    async def _fake_get_user_balance(_user_id: int):
        return 500

    async def _fake_get_user_devices(_user_id: int):
        return []

    async def _panel_ok(**kwargs):
        return "vless://link", client_uuid, None

    async def _db_fail(**kwargs):
        raise RuntimeError("db insert failed")

    rollback = AsyncMock(return_value=(True, None))

    async def _fake_raise_user_action_error(**kwargs):
        raise HTTPException(status_code=kwargs["status_code"], detail=kwargs["detail"])

    monkeypatch.setattr(api, "_get_user_or_404", _fake_get_user_or_404)
    monkeypatch.setattr(api, "get_user_balance", _fake_get_user_balance)
    monkeypatch.setattr(api, "get_user_devices", _fake_get_user_devices)
    monkeypatch.setattr(api, "add_device_to_panel", _panel_ok)
    monkeypatch.setattr(api, "add_device", _db_fail)
    monkeypatch.setattr(api, "rollback_orphan_panel_client", rollback)
    monkeypatch.setattr(api, "_raise_user_action_error", _fake_raise_user_action_error)

    with pytest.raises(HTTPException) as exc:
        await api.create_device(1, DeviceCreateIn(device_name="iphone"))

    assert exc.value.status_code == 500
    assert exc.value.detail == api._INTERNAL_ACTION_ERROR
    rollback.assert_awaited_once_with(
        1,
        client_uuid,
        context="api.create_device",
    )


@pytest.mark.asyncio
async def test_bot_add_device_rollbacks_panel_on_db_fail(monkeypatch):
    client_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    message = MagicMock()
    message.from_user.id = 42
    message.from_user.username = "alice"
    message.text = "iPhone"
    message.answer = AsyncMock()

    state = AsyncMock()

    monkeypatch.setattr(profile_module, "get_user", AsyncMock(return_value={"status": "ACTIVE"}))
    monkeypatch.setattr(
        profile_module,
        "add_device_to_panel",
        AsyncMock(return_value=("vless://link", client_uuid, None)),
    )
    monkeypatch.setattr(
        profile_module,
        "add_device",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    rollback = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(profile_module, "rollback_orphan_panel_client", rollback)

    await profile_module.process_device_name(message, state)

    rollback.assert_awaited_once_with(
        42,
        client_uuid,
        context="bot.add_device",
    )
    state.clear.assert_awaited()
    message.answer.assert_awaited_once()
    assert "не удалось" in message.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_rollback_orphan_panel_client_skips_empty_uuid():
    ok, err = await vpn_module.rollback_orphan_panel_client(
        1,
        None,
        context="test",
    )
    assert ok is True
    assert err is None


@pytest.mark.asyncio
async def test_rollback_orphan_panel_client_calls_remove(monkeypatch):
    remove = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(vpn_module, "remove_device_from_panel", remove)

    ok, err = await vpn_module.rollback_orphan_panel_client(
        7,
        "uuid-7",
        context="test.context",
    )

    assert ok is True
    assert err is None
    remove.assert_awaited_once_with("uuid-7", 7)
