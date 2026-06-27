from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import User

from app.bot.handlers import common as common_module


def _start_message(user_id: int = 42) -> MagicMock:
    message = MagicMock()
    message.from_user = User(
        id=user_id,
        is_bot=False,
        first_name="Alice",
        username="alice",
    )
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_start_dedup_sends_notice_without_db_call(monkeypatch):
    message = _start_message()
    command = MagicMock()
    command.args = None

    common_module._recent_start.clear()
    common_module._recent_start[42] = True

    get_user = AsyncMock()
    monkeypatch.setattr(common_module, "get_user_data_dict", get_user)

    await common_module.start_cmd(message, command)

    message.answer.assert_awaited_once()
    assert "обрабатываю" in message.answer.await_args.args[0].lower()
    get_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_dedup_does_not_block_topup_deep_link(monkeypatch):
    message = _start_message()
    command = MagicMock()
    command.args = "topup"

    common_module._recent_start.clear()
    common_module._recent_start[42] = True

    monkeypatch.setattr(common_module, "get_user_data_dict", AsyncMock(return_value={"user_id": 42}))
    with patch(
        "app.bot.handlers.payments.send_payment_options_message",
        AsyncMock(),
    ) as send_payment:
        await common_module.start_cmd(message, command)

    send_payment.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_start_new_user_sets_trial_expire_at(monkeypatch):
    from datetime import datetime, timedelta, timezone

    message = _start_message(user_id=777)
    command = MagicMock()
    command.args = None
    common_module._recent_start.clear()

    save_user = AsyncMock()
    monkeypatch.setattr(common_module, "get_user_data_dict", AsyncMock(return_value=None))
    monkeypatch.setattr(common_module, "save_user", save_user)
    monkeypatch.setattr(common_module, "TRIAL_DAYS", 1)
    monkeypatch.setattr(common_module, "main_kb", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(common_module, "bot", MagicMock())
    common_module.bot.delete_message = AsyncMock()

    before = datetime.now(timezone.utc)
    await common_module.start_cmd(message, command)
    after = datetime.now(timezone.utc)

    save_user.assert_awaited_once()
    expire_at = save_user.await_args.kwargs["expire_at"]
    assert expire_at > before + timedelta(hours=23)
    assert expire_at <= after + timedelta(days=1, minutes=1)
