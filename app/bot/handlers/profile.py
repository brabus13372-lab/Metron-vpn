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
from app.config import ADMIN_ID, DEVICE_MONTHLY_COST, TZ_MSK, TZ_NSK, WEBAPP_URL

from app.db import (
    get_user_data_dict as get_user,
    get_user_devices,
    add_device,
    remove_device as delete_device,
    get_device_by_id,
)

from app.services.vpn import (
    add_device_to_panel,
    remove_device_from_panel,
    rollback_orphan_panel_client,
)
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


def _profile_text(user: dict, devices: list, user_id: int) -> str:
    days = _days_left(user.get("expires_at"))
    days_str = f"{days} дн." if days is not None else "—"
    status_emoji = {"ACTIVE": "✅", "TRIAL": "🌟", "INACTIVE": "❌", "BANNED": "🚫"}.get(
        user.get("status", ""), "❓"
    )
    return (
        f"<b>👤 Профиль</b>\n\n"
        f"ID: <code>{user_id}</code>\n"
        f"Статус: {status_emoji} {user.get('status', '?')}\n"
        f"Баланс: <b>{_fmt_balance(user.get('balance', 0))} ₽</b>\n"
        f"До конца подписки: <b>{days_str}</b>\n"
        f"Устройств: <b>{len(devices)}/{MAX_DEVICES}</b>\n"
    )


@dp.message(F.text == "👤 Профиль")
async def cmd_profile(message: types.Message):
    """Оставляем обратную совместимость для старых reply-кнопок."""
    user_id = message.from_user.id
    user = await _get_or_warn(message, user_id)
    if not user:
        return
    devices = await get_user_devices(user_id)
    await message.answer(
        _profile_text(user, devices, user_id),
        parse_mode="HTML",
        reply_markup=keyboards.webapp_button(WEBAPP_URL, user_id),
    )


@dp.callback_query(F.data == "devices_list")
async def cb_devices_list(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = await _get_or_warn(callback.message, user_id)
    if not user:
        await callback.answer()
        return
    try:
        devices = await get_user_devices(user_id)
    except Exception:
        logger.exception("devices_list.db_failed user_id=%s", user_id)
        await callback.answer("❌ Не удалось загрузить устройства", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=keyboards.build_devices_keyboard(devices))
    await callback.answer()


@dp.callback_query(F.data.startswith("device_detail_"))
async def cb_device_detail(callback: types.CallbackQuery):
    try:
        device_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректный запрос")
        return
    device = await get_device_by_id(device_id)
    if not device or device["user_id"] != callback.from_user.id:
        await callback.answer("❌ Устройство не найдено")
        return
    vless = device.get("vless_link", "—")
    text = (
        f"<b>📱 {html.escape(device['device_name'])}</b>\n\n"
        f"VLESS ключ:\n<code>{html.escape(vless)}</code>"
    )
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboards.build_device_detail_keyboard(device_id),
    )
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
    if not message.text:
        await message.answer("❌ Отправьте текстовое название устройства.")
        return
    device_name = message.text.strip()[:50]
    if not device_name:
        await message.answer("❌ Название не может быть пустым.")
        return

    user = await get_user(user_id)
    if not user or user.get("status") not in ("ACTIVE", "TRIAL"):
        await state.clear()
        await message.answer("❌ Добавление устройства доступно только активным пользователям.")
        return

    username = message.from_user.username or str(user_id)
    client_uuid: str | None = None
    vless_link: str | None = None
    try:
        vless_link, client_uuid, err = await add_device_to_panel(user_id, username, device_name)
        if err:
            logger.error("add_device.panel_error user_id=%s err=%s", user_id, err)
            await state.clear()
            await message.answer("❌ Ошибка при создании клиента. Попробуйте позже.")
            return
        await add_device(user_id, device_name, client_uuid, vless_link)
    except Exception:
        logger.exception("add_device.failed user_id=%s", user_id)
        await rollback_orphan_panel_client(
            user_id,
            client_uuid,
            context="bot.add_device",
        )
        await state.clear()
        await message.answer("❌ Не удалось добавить устройство. Попробуйте позже.")
        return

    await state.clear()
    await message.answer(
        f"✅ Устройство <b>{html.escape(device_name)}</b> добавлено!\n\n"
        f"VLESS ключ:\n<code>{html.escape(vless_link)}</code>\n\n"
        "Для управления устройствами используйте личный кабинет 👇",
        parse_mode="HTML",
        reply_markup=keyboards.webapp_button(WEBAPP_URL, user_id),
    )


# Шаг 1: показываем кнопку подтверждения
# callback_data: "device_delete_confirm_{id}"  — не пересекается с "device_delete_{id}"
@dp.callback_query(F.data.startswith("device_delete_confirm_"))
async def cb_device_delete_confirm(callback: types.CallbackQuery):
    try:
        device_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректный запрос")
        return
    device = await get_device_by_id(device_id)
    if not device or device["user_id"] != callback.from_user.id:
        await callback.answer("❌ Устройство не найдено")
        return
    await callback.message.edit_text(
        f"⚠️ Удалить <b>{html.escape(device['device_name'])}</b>?\n\n"
        "Ключ будет удалён из панели и перестанет работать.",
        parse_mode="HTML",
        reply_markup=keyboards.build_confirm_delete_keyboard(device_id),
    )
    await callback.answer()


# Шаг 2: фактическое удаление
# callback_data: "device_delete_{id}"
@dp.callback_query(F.data.startswith("device_delete_") & ~F.data.startswith("device_delete_confirm_"))
async def cb_device_delete(callback: types.CallbackQuery):
    try:
        device_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректный запрос")
        return
    device = await get_device_by_id(device_id)
    if not device or device["user_id"] != callback.from_user.id:
        await callback.answer("❌ Устройство не найдено")
        return

    client_uuid = device.get("client_uuid")

    # Удаляем из панели — только если клиент ещё активен (is_active=True)
    if client_uuid and device.get("is_active"):
        ok, err = await remove_device_from_panel(client_uuid, callback.from_user.id)
        if not ok:
            # Панель недоступна — не удаляем из БД, чтобы reconcile потом вычистил 
            logger.error(
                "delete_device.panel_fail device_id=%s uuid=%s err=%s",
                device_id, client_uuid, err,
            )
            await callback.answer(
                "❌ Ошибка панели — попробуйте позже",
                show_alert=True,
            )
            return

    # Хард делит из БД
    try:
        await delete_device(device_id)
    except Exception:
        logger.exception(
            "delete_device.db_fail device_id=%s uuid=%s",
            device_id, client_uuid,
        )
        await callback.answer(
            "❌ Не удалось удалить устройство. Попробуйте позже.",
            show_alert=True,
        )
        return

    logger.info(
        "delete_device.done user_id=%s device_id=%s uuid=%s",
        callback.from_user.id, device_id, client_uuid,
    )
    await callback.message.edit_text(
        f"✅ Устройство <b>{html.escape(device['device_name'])}</b> удалено.\n\n"
        "Для управления устройствами используйте личный кабинет 👇",
        parse_mode="HTML",
        reply_markup=keyboards.webapp_button(WEBAPP_URL, callback.from_user.id),
    )
    await callback.answer()


@dp.callback_query(F.data == "back")
async def cb_back(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    try:
        user = await get_user(user_id)
        devices = await get_user_devices(user_id)
    except Exception:
        logger.exception("profile.back.db_failed user_id=%s", user_id)
        await callback.answer("❌ Не удалось загрузить профиль", show_alert=True)
        return
    text = _profile_text(user, devices, user_id) if user else "❌ Пользователь не найден."
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboards.webapp_button(WEBAPP_URL, user_id),
    )
    await callback.answer()
