import os


# Required for aiogram.Bot singleton creation during module imports.
os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_FOR_PYTEST")
