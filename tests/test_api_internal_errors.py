import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock, patch

import app.api as api
from app.schemas import DeviceCreateIn
from app.bot.handlers import payments as payments_module


def test_payment_options_markup_has_amount_buttons():
    markup = payments_module._payment_options_markup()
    assert markup.inline_keyboard
    assert any(
        btn.callback_data and btn.callback_data.startswith("pay_amount_")
        for row in markup.inline_keyboard
        for btn in row
    )


@pytest.mark.asyncio
async def test_start_topup_deep_link_routes_to_payment_options():
    from app.bot.handlers import common as common_module

    message = MagicMock()
    message.from_user.id = 42
    message.from_user.username = "alice"
    message.from_user.first_name = "Alice"
    message.answer = AsyncMock()

    command = MagicMock()
    command.args = "topup"

    with patch.object(common_module, "_recent_start", {}):
        with patch.object(common_module, "get_user_data_dict", AsyncMock(return_value={"user_id": 42})):
            with patch(
                "app.bot.handlers.payments.send_payment_options_message",
                AsyncMock(),
            ) as send_payment:
                await common_module.start_cmd(message, command)

    send_payment.assert_awaited_once_with(message)
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotate_key_hides_internal_exception(monkeypatch):
    async def _fake_get_user_or_404(_user_id: int):
        return {
            "status": "TRIAL",
            "username": "alice",
            "vless_link": None,
            "uuid": None,
        }

    async def _fake_get_user_devices(_user_id: int):
        return []

    async def _boom(**kwargs):
        raise RuntimeError("panel login failed: secret=leak")

    async def _fake_raise_user_action_error(**kwargs):
        raise HTTPException(status_code=kwargs["status_code"], detail=kwargs["detail"])

    monkeypatch.setattr(api, "_get_user_or_404", _fake_get_user_or_404)
    monkeypatch.setattr(api, "get_user_devices", _fake_get_user_devices)
    monkeypatch.setattr(api, "rotate_user_key", _boom)
    monkeypatch.setattr(api, "_raise_user_action_error", _fake_raise_user_action_error)

    with pytest.raises(HTTPException) as exc:
        await api.rotate_key(1)

    assert exc.value.status_code == 500
    assert exc.value.detail == api._INTERNAL_ACTION_ERROR
    assert "secret" not in str(exc.value.detail)


@pytest.mark.asyncio
async def test_create_device_hides_panel_error(monkeypatch):
    async def _fake_get_user_or_404(_user_id: int):
        return {"status": "ACTIVE", "username": "alice"}

    async def _fake_get_user_balance(_user_id: int):
        return 500

    async def _fake_get_user_devices(_user_id: int):
        return []

    async def _panel_fail(**kwargs):
        return None, None, "Panel error: login failed secret=leak"

    async def _fake_raise_user_action_error(**kwargs):
        raise HTTPException(status_code=kwargs["status_code"], detail=kwargs["detail"])

    monkeypatch.setattr(api, "_get_user_or_404", _fake_get_user_or_404)
    monkeypatch.setattr(api, "get_user_balance", _fake_get_user_balance)
    monkeypatch.setattr(api, "get_user_devices", _fake_get_user_devices)
    monkeypatch.setattr(api, "add_device_to_panel", _panel_fail)
    monkeypatch.setattr(api, "_raise_user_action_error", _fake_raise_user_action_error)

    with pytest.raises(HTTPException) as exc:
        await api.create_device(1, DeviceCreateIn(device_name="iphone"))

    assert exc.value.status_code == 500
    assert exc.value.detail == api._INTERNAL_ACTION_ERROR
    assert "secret" not in str(exc.value.detail)


@pytest.mark.asyncio
async def test_create_device_hides_panel_exception(monkeypatch):
    async def _fake_get_user_or_404(_user_id: int):
        return {"status": "ACTIVE", "username": "alice"}

    async def _fake_get_user_balance(_user_id: int):
        return 500

    async def _fake_get_user_devices(_user_id: int):
        return []

    async def _boom(**kwargs):
        raise RuntimeError("panel login failed: secret=leak")

    async def _fake_raise_user_action_error(**kwargs):
        raise HTTPException(status_code=kwargs["status_code"], detail=kwargs["detail"])

    monkeypatch.setattr(api, "_get_user_or_404", _fake_get_user_or_404)
    monkeypatch.setattr(api, "get_user_balance", _fake_get_user_balance)
    monkeypatch.setattr(api, "get_user_devices", _fake_get_user_devices)
    monkeypatch.setattr(api, "add_device_to_panel", _boom)
    monkeypatch.setattr(api, "_raise_user_action_error", _fake_raise_user_action_error)

    with pytest.raises(HTTPException) as exc:
        await api.create_device(1, DeviceCreateIn(device_name="iphone"))

    assert exc.value.status_code == 500
    assert exc.value.detail == api._INTERNAL_ACTION_ERROR


@pytest.mark.asyncio
async def test_delete_device_hides_panel_error(monkeypatch):
    device = {
        "id": 10,
        "user_id": 1,
        "client_uuid": "uuid-10",
        "is_active": True,
    }

    async def _fake_get_device_by_id(_device_id: int):
        return device

    async def _noop_assert(*_args, **_kwargs):
        return None

    async def _panel_fail(*_args, **_kwargs):
        return False, "panel disable failed: secret=leak"

    async def _fake_raise_user_action_error(**kwargs):
        raise HTTPException(status_code=kwargs["status_code"], detail=kwargs["detail"])

    monkeypatch.setattr(api, "get_device_by_id", _fake_get_device_by_id)
    monkeypatch.setattr(api, "_assert_not_last_active_device", _noop_assert)
    monkeypatch.setattr(
        "app.core.panel_client.update_client_fields",
        _panel_fail,
    )
    monkeypatch.setattr(api, "_raise_user_action_error", _fake_raise_user_action_error)

    with pytest.raises(HTTPException) as exc:
        await api.delete_device(1, 10)

    assert exc.value.status_code == 502
    assert exc.value.detail == api._INTERNAL_ACTION_ERROR
    assert "secret" not in str(exc.value.detail)


@pytest.mark.asyncio
async def test_hard_delete_device_hides_panel_error(monkeypatch):
    device = {
        "id": 11,
        "user_id": 1,
        "client_uuid": "uuid-11",
        "is_active": True,
    }

    async def _fake_get_device_by_id(_device_id: int):
        return device

    async def _noop_assert(*_args, **_kwargs):
        return None

    async def _panel_fail(**kwargs):
        return False, "panel delete failed: secret=leak"

    async def _fake_raise_user_action_error(**kwargs):
        raise HTTPException(status_code=kwargs["status_code"], detail=kwargs["detail"])

    monkeypatch.setattr(api, "get_device_by_id", _fake_get_device_by_id)
    monkeypatch.setattr(api, "_assert_not_last_active_device", _noop_assert)
    monkeypatch.setattr(api, "remove_device_from_panel", _panel_fail)
    monkeypatch.setattr(api, "_raise_user_action_error", _fake_raise_user_action_error)

    with pytest.raises(HTTPException) as exc:
        await api.hard_delete_device(1, 11)

    assert exc.value.status_code == 502
    assert exc.value.detail == api._INTERNAL_ACTION_ERROR
    assert "secret" not in str(exc.value.detail)
