from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import User
from cachetools import TTLCache

from app.bot.handlers import support as support_module


def _support_message(user_id: int = 42, text: str = "Нужна помощь") -> MagicMock:
    message = MagicMock()
    message.from_user = User(
        id=user_id,
        is_bot=False,
        first_name="Alice",
        username="alice",
    )
    message.text = text
    message.caption = None
    message.photo = None
    message.video = None
    message.document = None
    message.voice = None
    message.video_note = None
    message.chat.id = user_id
    message.message_id = 100
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_support_forward_keeps_fsm_on_admin_send_failure(monkeypatch):
    message = _support_message()
    state = AsyncMock()
    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=RuntimeError("admin unreachable"))

    monkeypatch.setattr(support_module, "ADMIN_ID", 999)
    monkeypatch.setattr(support_module, "_last_support_message", TTLCache(maxsize=100, ttl=10.0))

    await support_module.forward_to_admin(message, state, bot)

    state.clear.assert_not_awaited()
    message.answer.assert_awaited_once()
    reply_text = message.answer.await_args.args[0]
    assert "ошибка" in reply_text.lower()
    assert "/cancel" in reply_text


@pytest.mark.asyncio
async def test_support_forward_clears_fsm_on_success(monkeypatch):
    message = _support_message()
    state = AsyncMock()
    bot = AsyncMock()
    bot.send_message = AsyncMock()

    monkeypatch.setattr(support_module, "ADMIN_ID", 999)
    monkeypatch.setattr(support_module, "_last_support_message", TTLCache(maxsize=100, ttl=10.0))

    await support_module.forward_to_admin(message, state, bot)

    state.clear.assert_awaited_once()
    message.answer.assert_awaited_once()
    assert "отправлено" in message.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_support_forward_allows_retry_after_failure(monkeypatch):
    message = _support_message(text="Первая попытка")
    state = AsyncMock()
    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=RuntimeError("temporary fail"))

    monkeypatch.setattr(support_module, "ADMIN_ID", 999)
    monkeypatch.setattr(support_module, "_last_support_message", TTLCache(maxsize=100, ttl=10.0))

    await support_module.forward_to_admin(message, state, bot)
    state.clear.assert_not_awaited()

    bot.send_message = AsyncMock()
    message.text = "Вторая попытка"
    message.answer.reset_mock()

    await support_module.forward_to_admin(message, state, bot)

    state.clear.assert_awaited_once()
    assert "отправлено" in message.answer.await_args.args[0].lower()
