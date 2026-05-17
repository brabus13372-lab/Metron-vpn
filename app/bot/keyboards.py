from aiogram import types
from aiogram.utils.keyboard import ReplyKeyboardBuilder


# --- КЛАВИАТУРА ---
def main_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="🚀 Подключить VPN"))
    builder.row(
        types.KeyboardButton(text="👤 Мой профиль"),
        types.KeyboardButton(text="📖 Инструкция")
    )
    builder.row(types.KeyboardButton(text="🆘 Поддержка"))
    return builder.as_markup(resize_keyboard=True)
