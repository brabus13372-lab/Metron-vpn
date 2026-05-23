import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from aiogram import F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from app.bot.dispatcher import dp
from app.config import ADMIN_ID, DEVICE_MONTHLY_COST, TZ_MSK, TZ_NSK

from app.db import (
    get_user_data_dict as get_user,
    get_user_devices,
    add_device,
    remove_device as delete_device,
)

async def get_device_by_id(device_id: int):
    from app.db import get_user_devices
    # временная заглушка — ищем устройство по id среди всех
    from app.db import get_db
    db = get_db()
    return await db.fetch_one("SELECT * FROM devices WHERE id = :id", {"id": device_id})

from app.services.vpn import (
    add_device_to_panel,
    remove_device_from_panel,
    rotate_user_key,
)
from app.core.vless import build_vless_link
from app.bot import keyboards

logger = logging.getLogger(__name__)

MAX_DEVICES = 5


class AddDeviceStates(StatesGroup):
    waiting_for_name = State()


def _fmt_balance(amount) -> str:
    try:
        return str(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except Exception:
        return str(amount)


def _days_left(expires_at) -> int | None:
    if expires_at is None:
        return None
    try:
        now = datetime.now(tz=timezone.utc)
        exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        return max((exp - now).days, 0)
    except Exception:
        return None


async def _get_or_warn(message: types.Message, user_id: int):
    user = await get_user(user_id)
    if not user:
        await message.answer("❌ Пользователь не найден. Используйте /start для регистрации.")
    return user


@dp.message(F.text == "👤 Профиль")
async def cmd_profile(message: types.Message):
    user_id = message.from_user.id
    user = await _get_or_warn(message, user_id)
    if not user:
        return
    devices = await get_user_devices(user_id)
    days = _days_left(user.get("expires_at"))
    days_str = f"{days} дн." if days is not None else "—"
    status_emoji = {"ACTIVE": "✅", "TRIAL": "🌟", "INACTIVE": "❌", "BANNED": "🚫"}.get(user.get("status", ""), "❓")
    text = (
        f"<b>👤 Профиль</b>\n\n"
        f"ID: <code>{user_id}</code>\n"
        f"Статус: {status_emoji} {user.get('status', '?')}\n"
        f"Баланс: <b>{_fmt_balance(user.get('balance', 0))} ₽</b>\n"
        f"До конца подписки: <b>{days_str}</b>\n"
        f"Устройств: <b>{len(devices)}/{MAX_DEVICES}</b>\n"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=keyboards.build_devices_keyboard(devices))


@dp.callback_query(F.data == "devices_list")
async def cb_devices_list(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = await _get_or_warn(callback.message, user_id)
    if not user:
        await callback.answer()
        return
    devices = await get_user_devices(user_id)
    await callback.message.edit_reply_markup(reply_markup=keyboards.build_devices_keyboard(devices))
    await callback.answer()


@dp.callback_query(F.data.startswith("device_detail_"))
async def cb_device_detail(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    device_id = int(parts[-1])
    device = await get_device_by_id(device_id)
    if not device or device["user_id"] != callback.from_user.id:
        await callback.answer("❌ Устройство не найдено")
        return
    vless = device.get("vless_link", "—")
    text = (
        f"<b>📱 {html.escape(device['device_name'])}</b>\n\n"
        f"VLESS ключ:\n<code>{html.escape(vless)}</code>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboards.build_device_detail_keyboard(device_id))
    await callback.answer()


@dp.callback_query(F.data == "add_device")
async def cb_add_device(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    devices = await get_user_devices(user_id)
    if len(devices) >= MAX_DEVICES:
        await callback.answer(f"❌ Максимум {MAX_DEVICES} устройств", show_alert=True)
        return
    await state.set_state(AddDeviceStates.waiting_for_name)
    await callback.message.edit_text(
        "📝 Введите название устройства (например: iPhone, Ноутбук):",
        reply_markup=keyboards.build_back_keyboard(),
    )
    await callback.answer()


@dp.message(StateFilter(AddDeviceStates.waiting_for_name))
async def process_device_name(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    device_name = message.text.strip()[:50]
    if not device_name:
        await message.answer("❌ Название не может быть пустым.")
        return
    await state.clear()
    user = await get_user(user_id)
    if not user or user.get("status") not in ("ACTIVE", "TRIAL"):
        await message.answer("❌ Добавление устройства доступно только активным пользователям.")
        return
    username = message.from_user.username or str(user_id)
    vless_link, client_uuid, err = await add_device_to_panel(user_id, username, device_name)
    if err:
        logger.error("add_device.panel_error user_id=%s err=%s", user_id, err)
        await message.answer(f"❌ Ошибка при создании клиента: {err}")
        return
    await add_device(user_id, device_name, client_uuid, vless_link)
    await message.answer(
        f"✅ Устройство <b>{html.escape(device_name)}</b> добавлено!\n\nVLESS ключ:\n<code>{html.escape(vless_link)}</code>",
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("delete_device_confirm_"))
async def cb_delete_device_confirm(callback: types.CallbackQuery):
    device_id = int(callback.data.split("_")[-1])
    device = await get_device_by_id(device_id)
    if not device or device["user_id"] != callback.from_user.id:
        await callback.answer("❌ Устройство не найдено")
        return
    await callback.message.edit_text(
        f"⚠️ Удалить <b>{html.escape(device['device_name'])}</b>?",
        parse_mode="HTML",
        reply_markup=keyboards.build_confirm_delete_keyboard(device_id),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("delete_device_"))
async def cb_delete_device(callback: types.CallbackQuery):
    device_id = int(callback.data.split("_")[-1])
    device = await get_device_by_id(device_id)
    if not device or device["user_id"] != callback.from_user.id:
        await callback.answer("❌ Устройство не найдено")
        return
    client_uuid = device.get("client_uuid")
    if client_uuid:
        ok, err = await remove_device_from_panel(client_uuid, callback.from_user.id)
        if not ok:
            logger.warning("delete_device.panel_warn device_id=%s err=%s", device_id, err)
    await delete_device(device_id)
    devices = await get_user_devices(callback.from_user.id)
    await callback.message.edit_text(
        f"✅ Устройство <b>{html.escape(device['device_name'])}</b> удалено.",
        parse_mode="HTML",
        reply_markup=keyboards.build_devices_keyboard(devices),
    )
    await callback.answer()


@dp.callback_query(F.data == "back")
async def cb_back(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    user = await get_user(user_id)
    devices = await get_user_devices(user_id)
    days = _days_left(user.get("expires_at")) if user else None
    days_str = f"{days} дн." if days is not None else "—"
    status_emoji = {"ACTIVE": "✅", "TRIAL": "🌟", "INACTIVE": "❌", "BANNED": "🚫"}.get(
        user.get("status", "") if user else "", "❓"
    )
    text = (
        f"<b>👤 Профиль</b>\n\n"
        f"ID: <code>{user_id}</code>\n"
        f"Статус: {status_emoji} {user.get('status', '?') if user else '?'}\n"
        f"Баланс: <b>{_fmt_balance(user.get('balance', 0) if user else 0)} ₽</b>\n"
        f"До конца подписки: <b>{days_str}</b>\n"
        f"Устройств: <b>{len(devices)}/{MAX_DEVICES}</b>\n"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboards.build_devices_keyboard(devices))
    await callback.answer()
