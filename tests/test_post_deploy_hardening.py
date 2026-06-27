import pytest
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import User

from app.bot.handlers import payments as payments_module
from app.services import payment_sweeper as sweeper_module


def _pre_checkout_query(user_id: int = 42, amount_cents: int = 10000, payload_user_id: int = 42):
    q = MagicMock()
    q.id = "query-1"
    q.from_user = User(id=user_id, is_bot=False, first_name="Alice")
    q.total_amount = amount_cents
    q.invoice_payload = f"vpn_pay_{payload_user_id}_1710000000_{amount_cents}"
    return q


@pytest.mark.asyncio
async def test_pre_checkout_rejects_payload_user_mismatch(monkeypatch):
    bot = AsyncMock()
    q = _pre_checkout_query(user_id=42, payload_user_id=99)

    monkeypatch.setattr(payments_module, "PAYMENT_AMOUNTS", [10000])

    await payments_module.pre_checkout(q, bot)

    bot.answer_pre_checkout_query.assert_awaited_once_with(
        q.id,
        ok=False,
        error_message="Неверный получатель платежа",
    )


@pytest.mark.asyncio
async def test_pre_checkout_accepts_matching_payload_user(monkeypatch):
    bot = AsyncMock()
    q = _pre_checkout_query(user_id=42, payload_user_id=42)

    monkeypatch.setattr(payments_module, "PAYMENT_AMOUNTS", [10000])

    await payments_module.pre_checkout(q, bot)

    bot.answer_pre_checkout_query.assert_awaited_once_with(q.id, ok=True)


@pytest.mark.asyncio
async def test_sweep_received_payments_applies_pending(monkeypatch):
    apply_mock = AsyncMock(return_value={"already_applied": False, "balance": 100.0})
    monkeypatch.setattr(sweeper_module, "list_received_payment_ids", AsyncMock(return_value=[7, 8]))
    monkeypatch.setattr(sweeper_module, "apply_payment_topup_idempotent", apply_mock)

    applied, failed = await sweeper_module.sweep_received_payments(bot=None)

    assert applied == 2
    assert failed == 0
    assert apply_mock.await_count == 2


@pytest.mark.asyncio
async def test_rotate_device_key_uses_safe_rotate(monkeypatch):
    import app.api as api

    async def _fake_get_user_or_404(_user_id: int):
        return {"status": "ACTIVE"}

    async def _fake_get_device_by_id(_device_id: int):
        return {
            "id": 3,
            "user_id": 1,
            "client_uuid": "old",
            "device_name": "phone",
            "is_active": True,
        }

    safe_rotate = AsyncMock(return_value=("vless://new", "new-uuid", None))
    monkeypatch.setattr(api, "_get_user_or_404", _fake_get_user_or_404)
    monkeypatch.setattr(api, "get_user_balance", AsyncMock(return_value=500))
    monkeypatch.setattr(api, "get_user_devices", AsyncMock(return_value=[{"id": 3, "is_active": True}]))
    monkeypatch.setattr(api, "get_device_by_id", _fake_get_device_by_id)
    monkeypatch.setattr(api, "safe_rotate_device_key", safe_rotate)

    result = await api.rotate_device_key(1, 3)

    safe_rotate.assert_awaited_once()
    assert result.client_uuid == "new-uuid"
    assert result.vless_link == "vless://new"
