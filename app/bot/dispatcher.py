import logging

from aiogram import Dispatcher
from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)

dp = Dispatcher()


@dp.errors()
async def on_handler_error(event: ErrorEvent) -> bool:
    logger.exception(
        "bot.unhandled_handler_error update_id=%s",
        event.update.update_id if event.update else None,
        exc_info=event.exception,
    )
    update = event.update
    if not update:
        return True
    try:
        if update.message:
            await update.message.answer(
                "⚠️ Что-то пошло не так. Попробуйте ещё раз или откройте личный кабинет."
            )
        elif update.callback_query:
            await update.callback_query.answer(
                "⚠️ Ошибка. Попробуйте позже.",
                show_alert=True,
            )
    except Exception:
        logger.exception("bot.error_handler_notify_failed")
    return True

# Страховка: даже при импорте dp без main.py включаем маскировку логов
from app.core.logging_sanitizer import ensure_sanitized_logging  # noqa: E402
ensure_sanitized_logging()

# Импорт хендлеров для регистрации через декораторы
from app.bot.handlers import admin  # noqa: F401,E402
from app.bot.handlers import common  # noqa: F401,E402
from app.bot.handlers import payments  # noqa: F401,E402
from app.bot.handlers import profile  # noqa: F401,E402
from app.bot.handlers import support  # noqa: F401,E402
