"""
Тесты пользовательских сценариев.

Проверяют ключевые состояния без реальных БД/панели — всё через моки.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.api as api
from app.core.telegram_auth import TelegramInitDataError


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def _user(status: str = "ACTIVE", balance: int = 10000, **extra) -> dict:
    return {
        "user_id": 1,
        "status": status,
        "username": "alice",
        "expire_at": None,
        "vless_link": None,
        "uuid": None,
        **extra,
    }


def _device(device_id: int = 1, is_active: bool = True, reason: str | None = None) -> dict:
    return {
        "id": device_id,
        "user_id": 1,
        "device_name": f"device-{device_id}",
        "client_uuid": f"uuid-{device_id}",
        "vless_link": f"vless://link-{device_id}",
        "monthly_cost": 10000,
        "is_active": is_active,
        "disabled_at": None,
        "disabled_reason": reason,
    }


# ---------------------------------------------------------------------------
# Сценарий 1: Эффективный статус
# ---------------------------------------------------------------------------

class TestEffectiveStatus:
    def test_active_user_with_active_device(self):
        result = api._effective_user_status("ACTIVE", 500, [_device()])
        assert result == "ACTIVE"

    def test_active_user_no_devices_no_balance(self):
        result = api._effective_user_status("ACTIVE", 0, [])
        assert result == "EXPIRED"

    def test_active_user_no_devices_has_balance(self):
        result = api._effective_user_status("ACTIVE", 500, [])
        assert result == "ACTIVE"

    def test_trial_user_passes_through(self):
        result = api._effective_user_status("TRIAL", 0, [])
        assert result == "TRIAL"

    def test_expired_status_passes_through(self):
        result = api._effective_user_status("EXPIRED", 0, [])
        assert result == "EXPIRED"

    def test_active_user_with_only_inactive_device_no_balance(self):
        result = api._effective_user_status("ACTIVE", 0, [_device(is_active=False)])
        assert result == "EXPIRED"

    def test_active_user_with_only_inactive_device_has_balance(self):
        # Все устройства отключены пользователем, но баланс есть — считаем ACTIVE
        result = api._effective_user_status("ACTIVE", 200, [_device(is_active=False)])
        assert result == "ACTIVE"


# ---------------------------------------------------------------------------
# Сценарий 2: Расчёт дней до истечения баланса
# ---------------------------------------------------------------------------

class TestBillingDaysLeft:
    def test_days_left_normal(self):
        # 300 рублей / (300/30) в день = ровно 30 дней
        result = api._billing_days_left(300.0, 300.0)
        assert result == 30

    def test_days_left_zero_balance(self):
        assert api._billing_days_left(0, 300.0) == 0

    def test_days_left_zero_cost(self):
        assert api._billing_days_left(500.0, 0) == 0

    def test_days_left_rounds_down(self):
        # 50 рублей / (100/30=3.33) ≈ 15 дней
        result = api._billing_days_left(50.0, 100.0)
        assert result == 15

    def test_days_left_none_inputs(self):
        assert api._billing_days_left(None, None) == 0


# ---------------------------------------------------------------------------
# Сценарий 3: Платёжная идемпотентность (unit, без БД)
# ---------------------------------------------------------------------------

class TestPaymentIdempotency:
    @pytest.mark.asyncio
    async def test_apply_payment_topup_already_applied(self):
        """Повторный вызов apply_payment_topup_idempotent не двойная зачисляет баланс."""
        from app.db import payments as pay_module

        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            # payment row — статус уже APPLIED
            {
                "id": 1,
                "user_id": 1,
                "provider_charge_id": "pc_001",
                "amount": 10000,
                "status": pay_module.PAYMENT_STATUS_APPLIED,
                "idempotency_key": "payment:pc_001",
            },
            # user row
            {"balance": 20000, "status": "ACTIVE"},
        ]

        db_mock = MagicMock()
        db_mock.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        db_mock.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        db_mock._cents_to_rubles = lambda c: Decimal(c) / 100

        with patch("app.db.payments.get_db", return_value=db_mock):
            result = await pay_module.apply_payment_topup_idempotent(1)

        assert result["already_applied"] is True
        assert result["applied"] is False
        assert result["status_changed"] is False

    @pytest.mark.asyncio
    async def test_apply_payment_topup_recovers_expired_user(self):
        """Пополнение баланса EXPIRED пользователя атомарно ставит статус ACTIVE."""
        from app.db import payments as pay_module

        mock_conn = AsyncMock()
        # call sequence: fetchrow(payment), fetchrow(user), fetchval(idempotency),
        # fetchrow(UPDATE users), execute(INSERT tx), execute(UPDATE payment)
        mock_conn.fetchrow.side_effect = [
            {
                "id": 2,
                "user_id": 5,
                "provider_charge_id": "pc_002",
                "amount": 30000,
                "status": pay_module.PAYMENT_STATUS_RECEIVED,
                "idempotency_key": "payment:pc_002",
            },
            {"balance": 0, "status": "EXPIRED"},
            {"balance": 30000},  # result of UPDATE users RETURNING balance
        ]
        mock_conn.fetchval.return_value = None  # нет существующей balance_transaction

        db_mock = MagicMock()
        db_mock.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        db_mock.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        db_mock._cents_to_rubles = lambda c: Decimal(c) / 100

        with patch("app.db.payments.get_db", return_value=db_mock):
            result = await pay_module.apply_payment_topup_idempotent(2)

        assert result["applied"] is True
        assert result["status_changed"] is True
        assert result["balance"] == Decimal("300.00")

    @pytest.mark.asyncio
    async def test_apply_payment_topup_active_user_no_status_change(self):
        """Пополнение баланса ACTIVE пользователя не меняет статус."""
        from app.db import payments as pay_module

        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            {
                "id": 3,
                "user_id": 7,
                "provider_charge_id": "pc_003",
                "amount": 10000,
                "status": pay_module.PAYMENT_STATUS_RECEIVED,
                "idempotency_key": "payment:pc_003",
            },
            {"balance": 5000, "status": "ACTIVE"},
            {"balance": 15000},
        ]
        mock_conn.fetchval.return_value = None

        db_mock = MagicMock()
        db_mock.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        db_mock.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        db_mock._cents_to_rubles = lambda c: Decimal(c) / 100

        with patch("app.db.payments.get_db", return_value=db_mock):
            result = await pay_module.apply_payment_topup_idempotent(3)

        assert result["applied"] is True
        assert result["status_changed"] is False


# ---------------------------------------------------------------------------
# Сценарий 4: Удаление последнего устройства заблокировано
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_last_active_device_blocked_http(monkeypatch):
    import app.deps as deps
    import httpx

    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "extract_user_id", lambda *_a, **_k: 1)

    async def _fake_get_device_by_id(_did: int):
        return _device(device_id=1)

    async def _fake_get_user_devices(_uid: int):
        return [_device(device_id=1)]

    monkeypatch.setattr(api, "get_device_by_id", _fake_get_device_by_id)
    monkeypatch.setattr(api, "get_user_devices", _fake_get_user_devices)

    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(
            "/api/user/1/devices/1",
            headers={"X-Telegram-Init-Data": "dummy"},
        )
    assert res.status_code == 409


# ---------------------------------------------------------------------------
# Сценарий 5: Устройство другого пользователя — 404, не 403
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_other_user_device_returns_404(monkeypatch):
    """
    Попытка удалить чужое устройство должна вернуть 404 (не раскрывает,
    что устройство существует, но принадлежит кому-то другому).
    """
    import app.deps as deps
    import httpx

    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "extract_user_id", lambda *_a, **_k: 1)

    async def _fake_get_device_by_id(_did: int):
        d = _device(device_id=99)
        d["user_id"] = 9999  # другой владелец
        return d

    monkeypatch.setattr(api, "get_device_by_id", _fake_get_device_by_id)

    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.delete(
            "/api/user/1/devices/99",
            headers={"X-Telegram-Init-Data": "dummy"},
        )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Сценарий 6: Создание устройства для EXPIRED/INACTIVE пользователя — 403
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_device_blocked_for_expired_user(monkeypatch):
    import app.deps as deps
    import httpx

    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "extract_user_id", lambda *_a, **_k: 1)

    async def _fake_get_user_data_dict(_uid: int):
        return _user(status="EXPIRED")

    async def _fake_get_user_balance(_uid: int):
        return 0

    async def _fake_get_user_devices(_uid: int):
        return []

    monkeypatch.setattr(api, "get_user_data_dict", _fake_get_user_data_dict)
    monkeypatch.setattr(api, "get_user_balance", _fake_get_user_balance)
    monkeypatch.setattr(api, "get_user_devices", _fake_get_user_devices)

    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/user/1/devices",
            headers={"X-Telegram-Init-Data": "dummy"},
            json={"device_name": "my-device"},
        )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Сценарий 7: Ротация ключа для неактивного устройства — 409
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rotate_device_key_blocked_for_disabled_device(monkeypatch):
    import app.deps as deps
    import httpx

    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "extract_user_id", lambda *_a, **_k: 1)

    async def _fake_get_user_data_dict(_uid: int):
        return _user(status="ACTIVE")

    async def _fake_get_user_balance(_uid: int):
        return 10000

    async def _fake_get_user_devices(_uid: int):
        return [_device(is_active=True)]

    async def _fake_get_device_by_id(_did: int):
        return _device(device_id=1, is_active=False, reason="user_request")

    monkeypatch.setattr(api, "get_user_data_dict", _fake_get_user_data_dict)
    monkeypatch.setattr(api, "get_user_balance", _fake_get_user_balance)
    monkeypatch.setattr(api, "get_user_devices", _fake_get_user_devices)
    monkeypatch.setattr(api, "get_device_by_id", _fake_get_device_by_id)

    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/user/1/devices/1/rotate",
            headers={"X-Telegram-Init-Data": "dummy"},
        )
    assert res.status_code == 409


# ---------------------------------------------------------------------------
# Сценарий 8: Ротация account key для ACTIVE пользователя без устройств — 409
# (только TRIAL без устройств имеет право на /rotate-key)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rotate_account_key_blocked_for_active_non_trial(monkeypatch):
    import app.deps as deps
    import httpx

    monkeypatch.setattr(deps, "BOT_TOKEN", "token")
    monkeypatch.setattr(deps, "extract_user_id", lambda *_a, **_k: 1)

    async def _fake_get_user_data_dict(_uid: int):
        return _user(status="ACTIVE")

    async def _fake_get_user_devices(_uid: int):
        return []

    monkeypatch.setattr(api, "get_user_data_dict", _fake_get_user_data_dict)
    monkeypatch.setattr(api, "get_user_devices", _fake_get_user_devices)

    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/user/1/rotate-key",
            headers={"X-Telegram-Init-Data": "dummy"},
        )
    assert res.status_code == 409
