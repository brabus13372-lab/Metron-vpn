import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock

import app.api as api
from app.bot.handlers import admin as admin_module
from app.bot.handlers import payments as payments_module
from app.bot.handlers import profile as profile_module
from app.schemas import DeviceCreateIn


@pytest.mark.asyncio
async def test_send_invoice_handles_telegram_error(monkeypatch):
    call = MagicMock()
    call.from_user.id = 42
    call.data = "pay_amount_10000"
    call.answer = AsyncMock()
    call.message.answer = AsyncMock()
    bot = AsyncMock()
    bot.send_invoice = AsyncMock(side_effect=RuntimeError("telegram down"))

    monkeypatch.setattr(payments_module, "PAYMENT_AMOUNTS", [10000])
    monkeypatch.setattr(payments_module, "PAY_TOKEN", "token")

    await payments_module.send_invoice(call, bot)

    call.message.answer.assert_awaited_once()
    assert "счёт" in call.message.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_create_device_not_found_uses_internal_error(monkeypatch):
    client_uuid = "uuid-1"

    async def _fake_get_user_or_404(_user_id: int):
        return {"status": "ACTIVE", "username": "alice"}

    async def _fake_raise(**kwargs):
        raise HTTPException(status_code=kwargs["status_code"], detail=kwargs["detail"])

    monkeypatch.setattr(api, "_get_user_or_404", _fake_get_user_or_404)
    monkeypatch.setattr(api, "get_user_balance", AsyncMock(return_value=500))
    monkeypatch.setattr(api, "get_user_devices", AsyncMock(side_effect=[[], []]))
    monkeypatch.setattr(
        api,
        "add_device_to_panel",
        AsyncMock(return_value=("vless://x", client_uuid, None)),
    )
    monkeypatch.setattr(api, "add_device", AsyncMock(return_value=99))
    monkeypatch.setattr(api, "_raise_user_action_error", _fake_raise)

    with pytest.raises(HTTPException) as exc:
        await api.create_device(1, DeviceCreateIn(device_name="phone"))

    assert exc.value.detail == api._INTERNAL_ACTION_ERROR
    assert "not found" not in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_rotate_device_key_db_fail_uses_internal_error(monkeypatch):
    async def _fake_get_user_or_404(_user_id: int):
        return {"status": "ACTIVE"}

    async def _fake_get_device_by_id(_device_id: int):
        return {
            "id": 5,
            "user_id": 1,
            "client_uuid": "old-uuid",
            "device_name": "phone",
            "is_active": True,
        }

    async def _fake_raise(**kwargs):
        raise HTTPException(status_code=kwargs["status_code"], detail=kwargs["detail"])

    monkeypatch.setattr(api, "_get_user_or_404", _fake_get_user_or_404)
    monkeypatch.setattr(api, "get_user_balance", AsyncMock(return_value=500))
    monkeypatch.setattr(api, "get_user_devices", AsyncMock(return_value=[{"id": 5, "is_active": True}]))
    monkeypatch.setattr(api, "get_device_by_id", _fake_get_device_by_id)
    monkeypatch.setattr(
        api,
        "safe_rotate_device_key",
        AsyncMock(return_value=(None, None, "DB update failed (panel rolled back)")),
    )
    monkeypatch.setattr(api, "_raise_user_action_error", _fake_raise)

    with pytest.raises(HTTPException) as exc:
        await api.rotate_device_key(1, 5)

    assert exc.value.detail == api._INTERNAL_ACTION_ERROR


@pytest.mark.asyncio
async def test_admin_give_balance_survives_activation_failure(monkeypatch):
    message = MagicMock()
    message.from_user.id = 1
    message.text = "/give_balance 42 100"
    message.answer = AsyncMock()
    bot = AsyncMock()

    monkeypatch.setattr(admin_module, "ADMIN_ID", 1)
    monkeypatch.setattr(admin_module, "get_user_data_dict", AsyncMock(return_value={"status": "ACTIVE"}))
    monkeypatch.setattr(admin_module, "add_balance_atomic", AsyncMock(return_value=200.0))
    monkeypatch.setattr(admin_module, "get_user_devices", AsyncMock(return_value=[
        {"is_active": False, "disabled_reason": "insufficient_funds"},
    ]))
    monkeypatch.setattr(
        admin_module,
        "activate_all_user_devices",
        AsyncMock(side_effect=RuntimeError("panel down")),
    )
    monkeypatch.setattr(admin_module, "set_reactivation_notification_pending", AsyncMock())

    await admin_module.admin_give_balance(message, bot)

    texts = [c.args[0] for c in message.answer.call_args_list]
    assert any("начислено" in t for t in texts)
    assert any("реактивация" in t.lower() for t in texts)


@pytest.mark.asyncio
async def test_profile_back_handles_db_error(monkeypatch):
    callback = MagicMock()
    callback.from_user.id = 42
    callback.answer = AsyncMock()
    callback.message.edit_text = AsyncMock()
    state = AsyncMock()

    monkeypatch.setattr(profile_module, "get_user", AsyncMock(side_effect=RuntimeError("db")))
    monkeypatch.setattr(profile_module, "get_user_devices", AsyncMock())

    await profile_module.cb_back(callback, state)

    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True
    callback.message.edit_text.assert_not_awaited()
