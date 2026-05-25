from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup


def main_kb(webapp_url: str, user_id: int) -> InlineKeyboardMarkup:
    """Единственная клавиатура бота — открыть личный кабинет WebApp."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🌐 Личный кабинет",
            web_app=WebAppInfo(url=f"{webapp_url}?uid={user_id}"),
        )
    )
    return builder.as_markup()


def build_devices_keyboard(devices: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for dev in devices:
        status = "✅" if dev.get("is_active") else "❌"
        builder.row(
            types.InlineKeyboardButton(
                text=f"{status} {dev['device_name']}",
                callback_data=f"device_detail_{dev['id']}",
            )
        )
    builder.row(types.InlineKeyboardButton(text="➕ Добавить устройство", callback_data="add_device"))
    builder.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="back"))
    return builder.as_markup()


def build_device_detail_keyboard(device_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(
            text="🗑 Удалить устройство",
            callback_data=f"device_delete_confirm_{device_id}",
        )
    )
    builder.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="back"))
    return builder.as_markup()


def build_confirm_delete_keyboard(device_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=f"device_delete_{device_id}",
        ),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="back"),
    )
    return builder.as_markup()


def build_back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="back"))
    return builder.as_markup()


def webapp_button(webapp_url: str, user_id: int) -> InlineKeyboardMarkup:
    """Кнопка открытия WebApp — используется после платежа и других событий."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🌐 Открыть личный кабинет",
            web_app=WebAppInfo(url=f"{webapp_url}?uid={user_id}"),
        )
    )
    return builder.as_markup()
