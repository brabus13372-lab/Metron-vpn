from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import SuccessfulPayment, User

from app.bot.handlers import payments as payments_module


def _valid_payment(user_id: int = 42, amount_cents: int = 10000) -> SuccessfulPayment:
    return SuccessfulPayment(
        currency="RUB",
        total_amount=amount_cents,
        invoice_payload=f"vpn_pay_{user_id}_1710000000_{amount_cents}",
        telegram_payment_charge_id="tg_charge_1",
        provider_payment_charge_id="provider_charge_1",
    )


def _payment_message(user_id: int = 42, amount_cents: int = 10000) -> MagicMock:
    message = MagicMock()
    message.from_user = User(
        id=user_id,
        is_bot=False,
        first_name="Alice",
        username="alice",
    )
    message.successful_payment = _valid_payment(user_id, amount_cents)
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_success_payment_confirms_when_user_data_fetch_fails(monkeypatch):
    message = _payment_message()
    bot = AsyncMock()

    monkeypatch.setattr(payments_module, "ADMIN_ID", 0)
    monkeypatch.setattr(payments_module, "PAYMENT_AMOUNTS", [10000])
    monkeypatch.setattr(payments_module, "ensure_user_stub", AsyncMock())
    monkeypatch.setattr(
        payments_module,
        "record_payment_idempotent",
        AsyncMock(return_value={"payment": {"id": 1}}),
    )
    monkeypatch.setattr(
        payments_module,
        "apply_payment_topup_idempotent",
        AsyncMock(return_value={"balance": 150.0, "already_applied": False}),
    )
    monkeypatch.setattr(
        payments_module,
        "get_user_data_dict",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(payments_module, "_safe_alert_admin", AsyncMock())
    monkeypatch.setattr(payments_module, "_execute_activation", AsyncMock())

    await payments_module.success_payment(message, bot)

    message.answer.assert_awaited_once()
    sent_text = message.answer.await_args.kwargs.get("text") or message.answer.await_args.args[0]
    assert "Оплата прошла успешно" in sent_text
    assert "150.00" in sent_text
    assert "личный кабинет" in sent_text
    payments_module._execute_activation.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_payment_confirms_when_devices_fetch_fails(monkeypatch):
    message = _payment_message()
    bot = AsyncMock()

    monkeypatch.setattr(payments_module, "ADMIN_ID", 0)
    monkeypatch.setattr(payments_module, "PAYMENT_AMOUNTS", [10000])
    monkeypatch.setattr(payments_module, "ensure_user_stub", AsyncMock())
    monkeypatch.setattr(
        payments_module,
        "record_payment_idempotent",
        AsyncMock(return_value={"payment": {"id": 2}}),
    )
    monkeypatch.setattr(
        payments_module,
        "apply_payment_topup_idempotent",
        AsyncMock(return_value={"balance": 200.0, "already_applied": False}),
    )
    monkeypatch.setattr(
        payments_module,
        "get_user_data_dict",
        AsyncMock(return_value={"user_id": 42, "status": "ACTIVE", "uuid": None}),
    )
    monkeypatch.setattr(
        payments_module,
        "get_user_devices",
        AsyncMock(side_effect=RuntimeError("devices query failed")),
    )
    monkeypatch.setattr(payments_module, "_safe_alert_admin", AsyncMock())
    monkeypatch.setattr(payments_module, "_execute_activation", AsyncMock())

    await payments_module.success_payment(message, bot)

    message.answer.assert_awaited_once()
    sent_text = message.answer.await_args.kwargs.get("text") or message.answer.await_args.args[0]
    assert "200.00" in sent_text
    assert "поддержку" in sent_text
    payments_module._execute_activation.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_payment_still_runs_activation_when_devices_available(monkeypatch):
    message = _payment_message()
    bot = AsyncMock()
    devices = [
        {"id": 1, "is_active": False, "disabled_reason": "insufficient_funds"},
    ]

    monkeypatch.setattr(payments_module, "ADMIN_ID", 0)
    monkeypatch.setattr(payments_module, "PAYMENT_AMOUNTS", [10000])
    monkeypatch.setattr(payments_module, "ensure_user_stub", AsyncMock())
    monkeypatch.setattr(
        payments_module,
        "record_payment_idempotent",
        AsyncMock(return_value={"payment": {"id": 3}}),
    )
    monkeypatch.setattr(
        payments_module,
        "apply_payment_topup_idempotent",
        AsyncMock(return_value={"balance": 300.0, "already_applied": False, "status_changed": True}),
    )
    monkeypatch.setattr(
        payments_module,
        "get_user_data_dict",
        AsyncMock(return_value={"user_id": 42, "status": "ACTIVE", "uuid": None}),
    )
    monkeypatch.setattr(payments_module, "get_user_devices", AsyncMock(return_value=devices))
    monkeypatch.setattr(payments_module, "_execute_activation", AsyncMock(return_value=(1, 0, [])))
    monkeypatch.setattr(payments_module, "set_reactivation_notification_pending", AsyncMock())
    monkeypatch.setattr(payments_module, "_safe_alert_admin", AsyncMock())

    await payments_module.success_payment(message, bot)

    payments_module._execute_activation.assert_awaited_once_with(42, bot)
    message.answer.assert_awaited_once()
    sent_text = message.answer.await_args.kwargs.get("text") or message.answer.await_args.args[0]
    assert "Доступ восстановлен" in sent_text


def test_build_payment_success_text_post_apply_issue():
    text = payments_module._build_payment_success_text(
        new_balance=100.0,
        has_devices=False,
        suspended_count=0,
        activation_success=0,
        activation_failed=0,
        post_apply_issues=["devices"],
    )
    assert "100.00" in text
    assert "поддержку" in text


def test_build_payment_apply_pending_text():
    text = payments_module._build_payment_apply_pending_text(10000)
    assert "100.00" in text
    assert "Оплата получена" in text
    assert "поддержку" in text


@pytest.mark.asyncio
async def test_apply_payment_with_retry_succeeds_on_second_attempt(monkeypatch):
    apply_mock = AsyncMock(
        side_effect=[
            RuntimeError("transient db"),
            {"balance": 150.0, "already_applied": False},
        ],
    )
    monkeypatch.setattr(payments_module, "apply_payment_topup_idempotent", apply_mock)

    result = await payments_module._apply_payment_with_retry(99)

    assert result["balance"] == 150.0
    assert apply_mock.await_count == 2


@pytest.mark.asyncio
async def test_success_payment_retries_apply_and_confirms(monkeypatch):
    message = _payment_message()
    bot = AsyncMock()
    apply_mock = AsyncMock(
        side_effect=[
            RuntimeError("transient"),
            {"balance": 250.0, "already_applied": False},
        ],
    )

    monkeypatch.setattr(payments_module, "ADMIN_ID", 0)
    monkeypatch.setattr(payments_module, "PAYMENT_AMOUNTS", [10000])
    monkeypatch.setattr(payments_module, "ensure_user_stub", AsyncMock())
    monkeypatch.setattr(
        payments_module,
        "record_payment_idempotent",
        AsyncMock(return_value={"payment": {"id": 10}}),
    )
    monkeypatch.setattr(payments_module, "apply_payment_topup_idempotent", apply_mock)
    monkeypatch.setattr(
        payments_module,
        "get_user_data_dict",
        AsyncMock(return_value={"user_id": 42, "status": "ACTIVE", "uuid": None}),
    )
    monkeypatch.setattr(payments_module, "get_user_devices", AsyncMock(return_value=[]))
    monkeypatch.setattr(payments_module, "_safe_alert_admin", AsyncMock())

    await payments_module.success_payment(message, bot)

    assert apply_mock.await_count == 2
    message.answer.assert_awaited_once()
    sent_text = message.answer.await_args.kwargs.get("text") or message.answer.await_args.args[0]
    assert "250.00" in sent_text
    assert "Оплата прошла успешно" in sent_text


@pytest.mark.asyncio
async def test_success_payment_pending_message_when_apply_fails_after_retry(monkeypatch):
    message = _payment_message()
    bot = AsyncMock()
    apply_mock = AsyncMock(side_effect=RuntimeError("permanent db fail"))
    alert_mock = AsyncMock()

    monkeypatch.setattr(payments_module, "ADMIN_ID", 0)
    monkeypatch.setattr(payments_module, "PAYMENT_AMOUNTS", [10000])
    monkeypatch.setattr(payments_module, "ensure_user_stub", AsyncMock())
    monkeypatch.setattr(
        payments_module,
        "record_payment_idempotent",
        AsyncMock(return_value={"payment": {"id": 55}}),
    )
    monkeypatch.setattr(payments_module, "apply_payment_topup_idempotent", apply_mock)
    monkeypatch.setattr(payments_module, "_safe_alert_admin", alert_mock)
    monkeypatch.setattr(payments_module, "get_user_data_dict", AsyncMock())

    await payments_module.success_payment(message, bot)

    assert apply_mock.await_count == 2
    message.answer.assert_awaited_once()
    sent_text = message.answer.await_args.kwargs.get("text") or message.answer.await_args.args[0]
    assert "Оплата получена" in sent_text
    assert "100.00" in sent_text
    assert "Оплата прошла успешно" not in sent_text
    payments_module.get_user_data_dict.assert_not_awaited()
    alert_mock.assert_awaited()
    alert_body = alert_mock.await_args.args[1]
    assert "payment_id=55" in alert_body
