"""Singleton Bot instance — импортируй в любом месте, где нужен bot.send_message."""
from aiogram import Bot
from app.config import BOT_TOKEN

bot: Bot = Bot(token=BOT_TOKEN)
