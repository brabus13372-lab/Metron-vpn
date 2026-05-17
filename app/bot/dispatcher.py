from aiogram import Dispatcher

dp = Dispatcher()

# Страховка: даже при импорте dp без main.py включаем маскировку логов
from app.logging_sanitizer import ensure_sanitized_logging  # noqa: E402
ensure_sanitized_logging()

# Импорт хендлеров для регистрации через декораторы
from app.bot.handlers import admin  # noqa: F401,E402
from app.bot.handlers import common  # noqa: F401,E402
from app.bot.handlers import payments  # noqa: F401,E402
from app.bot.handlers import profile  # noqa: F401,E402
from app.bot.handlers import support  # noqa: F401,E402

