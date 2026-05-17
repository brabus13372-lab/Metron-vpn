import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from aiogram import Bot, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.dispatcher import dp
from app.config import ADMIN_ID, TZ_MSK, TZ_NSK, DEVICE_MONTHLY_COST
from app.db import (
    get_user_data_dict, update_user_link,
    get_user_balance, get_user_devices,
    add_device, remove_device
)
from app.services.vpn import rotate_client_uuid, add_device_to_panel, remove_device_from_panel
from app.vless import build_vless_link

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------

class DeviceState(StatesGroup):
    waiting_device_name = State()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_profile_kb(user_id: int) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📖 Инструкция",                   callback_data="show_instruction"))
    builder.row(types.InlineKeyboardButton(text="🔄 Обновить ключ",                callback_data="update_my_key"))
    builder.row(types.InlineKeyboardButton(text="💳 Продлить подписку / Пополнить", callback_data="buy_vpn"))
    builder.row(types.InlineKeyboardButton(text="📱 Мои устройства",               callback_data="manage_devices"))
    if user_id == ADMIN_ID:
        builder.row(types.InlineKeyboardButton(text="🛠 Административное меню",    callback_data="admin_menu"))
    return builder.as_markup()


def _truncate_caption(header: str, body: str, limit: int = 1024) -> str:
    """Обрезает body так, чтобы header + body не превышали limit символов."""
    max_body = limit - len(header) - 3
    if len(body) > max_body:
        return header + body[:max_body] + "..."
    return header + body


async def _render_profile(user_id: int) -> tuple[str, types.InlineKeyboardMarkup]:
    """Возвращает (текст профиля, клавиатура). Вынесено, чтобы не дублировать."""
    user    = await asyncio.to_thread(get_user_data_dict, user_id)
    balance: Decimal = await asyncio.to_thread(get_user_balance, user_id) or Decimal("0.0")

    if not user:
        text = "<b>⚠️ Профиль не найден.</b>\nНажмите «🚀 Подключить VPN», чтобы создать ключ."
        return text, _build_profile_kb(user_id)

    expire_raw   = user.get("expire_at")
    vless_link   = user.get("vless_link")
    user_status  = user.get("status", "")

    now = datetime.now(timezone.utc)
    is_active = False
    has_positive_balance = balance > Decimal("0")
    expiry_nsk = expiry_msk = "Не активна"

    if expire_raw:
        try:
            dt = datetime.strptime(expire_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            expiry_nsk = dt.astimezone(TZ_NSK).strftime("%d.%m.%Y %H:%M")
            expiry_msk = dt.astimezone(TZ_MSK).strftime("%d.%m.%Y %H:%M")
            is_active = dt > now and has_positive_balance
        except ValueError as e:
            logger.warning("Failed to parse expire_at '%s' for user %s: %s", expire_raw, user_id, e)
            expiry_nsk = expiry_msk = "Некорректная дата"

    devices = await asyncio.to_thread(get_user_devices, user_id)

    devices_text = ""
    if devices:
        for dev in devices:
            status_icon = "📱"
            uuid_str    = dev["client_uuid"]
            uuid_display = f"{uuid_str[:8]}..." if len(uuid_str) > 8 else uuid_str
            devices_text += (
                f"\n{status_icon} <b>{html.escape(dev['device_name'])}</b>\n"
                f"   UUID: <code>{uuid_display}</code>\n"
                f"   Стоимость: {dev['monthly_cost']:.2f} руб/мес\n"
                f"   Создано: {dev['created_at']}\n"
            )
    else:
        devices_text = "\n<i>У вас пока нет добавленных устройств</i>"

    if is_active:
        status_text = "✅ Активна"
    elif expire_raw and not has_positive_balance:
        status_text = "⚠️ Отключена (Недостаточно средств)"
    elif expire_raw:
        status_text = "❌ Неактивна (Срок истёк)"
    else:
        status_text = "❌ Неактивна (Нет активной подписки)"

    safe_link = html.escape(vless_link) if vless_link else "Нет ключа"

    text = (
        f"<b>👤 Личный кабинет</b>\n\n"
        f"<b>🆔 ID:</b> <code>{user_id}</code>\n"
        f"<b>💰 Баланс:</b> <b>{balance:.2f} руб.</b>\n\n"
        f"<b>📅 Подписка до:</b>\n"
        f"🕒 {expiry_nsk} <b>(Новосибирск)</b>\n"
        f"🕚 {expiry_msk} <b>(Москва)</b>\n\n"
        f"<b>🚀 Статус:</b> {status_text}\n\n"
        f"<b>🔑 Ваш основной ключ:</b>\n<code>{safe_link}</code>\n\n"
        f"<b>📱 Устройства:</b>{devices_text}\n\n"
        f"<i>💡 Если VPN перестал подключаться, попробуйте обновить основной ключ.</i>"
    )
    return text, _build_profile_kb(user_id)


# ---------------------------------------------------------------------------
# Профиль
# ---------------------------------------------------------------------------

@dp.message(F.text == "👤 Мой профиль")
async def show_profile(message: types.Message) -> None:
    text, kb = await _render_profile(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.callback_query(F.data == "back_to_profile")
async def back_to_profile(call: types.CallbackQuery) -> None:
    text, kb = await _render_profile(call.from_user.id)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await call.answer()


# ---------------------------------------------------------------------------
# Обновление ключа
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "update_my_key")
async def handle_update_key(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    user    = await asyncio.to_thread(get_user_data_dict, user_id)

    if not user or not user.get("uuid"):
        return await call.answer("❌ У вас ещё нет ключа для обновления.", show_alert=True)

    old_uuid    = user["uuid"]
    expire_raw  = user.get("expire_at")
    clean_username = user.get("username") or call.from_user.username or f"user_{user_id}"

    try:
        expire_dt  = datetime.strptime(expire_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        target_ts  = int(expire_dt.timestamp() * 1000)
    except (ValueError, TypeError):
        target_ts  = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000)

    success, new_uuid, err = await rotate_client_uuid(
        old_uuid,
        user_id,
        clean_username,
        target_ts,
    )

    if success and new_uuid:
        link = build_vless_link(new_uuid, clean_username)
        await asyncio.to_thread(update_user_link, user_id, link, new_uuid)
        logger.info("Key successfully rotated for user %s", user_id)
        await call.message.answer(
            f"<b>✅ Ключ успешно обновлён!</b>\n\n"
            f"Предыдущий ключ деактивирован.\n"
            f"Ваш новый ключ:\n<code>{html.escape(link)}</code>",
            parse_mode="HTML",
        )
        await call.answer("Готово! 🚀")
    else:
        logger.error("Rotate key error for %s: %s", user_id, err)
        await call.answer("❌ Ошибка обновления ключа. Сервер недоступен.", show_alert=True)


# ---------------------------------------------------------------------------
# Управление устройствами — меню
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "manage_devices")
async def manage_devices_menu(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    balance: Decimal = await asyncio.to_thread(get_user_balance, user_id) or Decimal("0.0")
    devices = await asyncio.to_thread(get_user_devices, user_id)

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Добавить устройство", callback_data="device_add"))

    for dev in devices:
        builder.row(types.InlineKeyboardButton(
            text=f"📄 {html.escape(dev['device_name'])} ({dev['monthly_cost']:.2f}р/мес)",
            callback_data=f"device_info_{dev['id']}",
        ))

    builder.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_profile"))

    text = (
        f"<b>📱 Управление устройствами</b>\n\n"
        f"<b>💰 Текущий баланс:</b> {balance:.2f} руб.\n\n"
        f"<i>Каждое устройство — отдельный клиент в VPN. "
        f"Списание пропорционально стоимости устройства.</i>"
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await call.answer()


# ---------------------------------------------------------------------------
# Добавление устройства (FSM aiogram 3.x)
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "device_add")
async def device_add_start(call: types.CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id

    user = await asyncio.to_thread(get_user_data_dict, user_id)
    if not user or not user.get("uuid") or not user.get("vless_link"):
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(
            text="💳 Подключить VPN",
            callback_data="buy_vpn",
        ))
        builder.row(types.InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="manage_devices",
        ))

        await call.message.edit_text(
            "<b>📱 Сначала подключите VPN</b>\n\n"
            "Чтобы добавить устройство, сначала нужно оформить первый VPN-ключ.",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        await call.answer()
        return

    balance: Decimal = await asyncio.to_thread(get_user_balance, user_id) or Decimal("0.0")

    if balance <= 0:
        return await call.answer("❌ На балансе недостаточно средств. Пополните счёт.", show_alert=True)

    monthly_cost = DEVICE_MONTHLY_COST
    daily_cost   = (monthly_cost / Decimal(30)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="◀️ Отмена", callback_data="manage_devices"))

    await state.set_state(DeviceState.waiting_device_name)

    text = (
        f"<b>➕ Добавление нового устройства</b>\n\n"
        f"<b>💰 Баланс:</b> {balance:.2f} руб.\n\n"
        f"Введите название (например: <i>iPhone</i>, <i>Ноутбук</i>).\n\n"
        f"<i>Стоимость — {monthly_cost:.2f} руб/мес (~{daily_cost:.2f} руб/день).</i>\n\n"
        f"Для отмены нажмите кнопку ниже или /cancel"
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await call.answer()


@dp.message(DeviceState.waiting_device_name, F.text == "/cancel")
async def device_add_cancel(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Добавление устройства отменено.")


@dp.message(DeviceState.waiting_device_name, F.text)
async def device_add_confirm(message: types.Message, state: FSMContext) -> None:
    user_id      = message.from_user.id
    clean_device_name = message.text.strip()

    if not clean_device_name or len(clean_device_name) > 50:
        return await message.answer("❌ Название устройства должно быть от 1 до 50 символов.")

    # Актуальный баланс из БД — не из кэша состояния
    balance: Decimal = await asyncio.to_thread(get_user_balance, user_id) or Decimal("0.0")
    if balance <= 0:
        await state.clear()
        return await message.answer("❌ На балансе недостаточно средств.")

    clean_username = message.from_user.username or f"user_{user_id}"

    vless_link, client_uuid, error = await add_device_to_panel(
        user_id,
        clean_username,
        clean_device_name,
        days=30,
    )

    if error or not vless_link or not client_uuid:
        logger.warning(
            "device_add_confirm: panel failed for user_id=%s device=%r error=%r",
            user_id,
            clean_device_name,
            error,
        )
        await state.clear()
        await message.answer(
            "❌ Не удалось создать устройство: панель недоступна или вернула ошибку.\n"
            "Попробуйте позже.",
        )
        return

    monthly_cost_cents = int(DEVICE_MONTHLY_COST * 100)
    dev_id = await asyncio.to_thread(
        add_device, user_id, clean_device_name, client_uuid, vless_link,
        monthly_cost_cents=monthly_cost_cents
    )

    if not dev_id:
        logger.error("Failed to insert device '%s' for user %s into DB.", clean_device_name, user_id)

        try:
            await remove_device_from_panel(client_uuid, user_id)
        except Exception:
            logger.exception(
                "Failed to rollback panel device after DB insert error user_id=%s client_uuid=%s",
                user_id,
                client_uuid,
            )

        await state.clear()
        return await message.answer("❌ Ошибка сохранения устройства. Попробуйте позже.")

    await state.clear()

    logger.info("Device '%s' (ID: %s) added for user %s", clean_device_name, dev_id, user_id)

    monthly_cost = DEVICE_MONTHLY_COST
    daily_cost   = (monthly_cost / Decimal(30)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="◀️ К устройствам", callback_data="manage_devices"))

    header = "✅ <b>Устройство успешно добавлено!</b>\n\n"
    text = (
        f"{header}"
        f"<b>📱 Название:</b> {html.escape(clean_device_name)}\n"
        f"<b>💰 Стоимость:</b> {monthly_cost:.2f} руб/мес (~{daily_cost:.2f} руб/день)\n"
        f"<b>🔑 Ключ:</b>\n<code>{html.escape(vless_link)}</code>\n\n"
        f"<i>Ежедневно будет списываться ~{daily_cost:.2f} руб. с вашего баланса.</i>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


# ---------------------------------------------------------------------------
# Информация об устройстве
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("device_info_"))
async def device_info(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    try:
        dev_id = int(call.data.split("_")[2])
    except (IndexError, ValueError):
        return await call.answer("❌ Ошибка данных", show_alert=True)

    devices = await asyncio.to_thread(get_user_devices, user_id)
    device  = next((d for d in devices if d["id"] == dev_id), None)

    if not device:
        return await call.answer("❌ Устройство не найдено", show_alert=True)

    monthly_cost = device["monthly_cost"]
    daily_cost   = (monthly_cost / Decimal(30)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🗑️ Удалить устройство", callback_data=f"device_delete_{dev_id}"))
    builder.row(types.InlineKeyboardButton(text="◀️ Назад",              callback_data="manage_devices"))

    text = (
        f"<b>📱 Устройство: {html.escape(device['device_name'])}</b>\n\n"
        f"<b>🆔 UUID:</b> <code>{device['client_uuid']}</code>\n"
        f"<b>💰 Стоимость:</b> {monthly_cost:.2f} руб/мес (~{daily_cost:.2f} руб/день)\n"
        f"<b>📅 Создано:</b> {device['created_at']}\n\n"
        f"<b>🔑 Ключ:</b>\n<code>{html.escape(device['vless_link'] or 'Нет')}</code>\n\n"
        f"<i>При удалении устройство будет отключено.</i>"
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await call.answer()


# ---------------------------------------------------------------------------
# Удаление устройства — подтверждение (регистрируем ПЕРВЫМ, до общего prefix)
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("device_delete_confirmed_"))
async def device_delete_final(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    try:
        dev_id = int(call.data.split("_")[3])
    except (IndexError, ValueError):
        return await call.answer("❌ Ошибка данных", show_alert=True)

    devices = await asyncio.to_thread(get_user_devices, user_id)
    device  = next((d for d in devices if d["id"] == dev_id), None)

    if not device:
        return await call.answer("❌ Устройство не найдено", show_alert=True)

    success, error = await remove_device_from_panel(device["client_uuid"], user_id)
    if not success:
        logger.warning(
            "Panel removal failed for %s: %s. Keeping DB record.",
            device["client_uuid"], error,
        )
        return await call.answer(
            "❌ Не удалось отключить устройство в панели. Попробуйте позже.",
            show_alert=True,
        )

    removed = await asyncio.to_thread(remove_device, dev_id, user_id)

    if removed:
        logger.info("Device '%s' (ID: %s) deleted for user %s", device["device_name"], dev_id, user_id)
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="manage_devices"))
        await call.message.edit_text(
            f"✅ <b>Устройство '{html.escape(device['device_name'])}' удалено</b>\n\n"
            f"Клиент деактивирован, запись удалена.",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
    else:
        logger.error("Failed to remove device %s from DB for user %s", dev_id, user_id)
        await call.answer("❌ Ошибка удаления из БД", show_alert=True)

    await call.answer()


@dp.callback_query(F.data.startswith("device_delete_"))
async def device_delete_confirm(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    try:
        dev_id = int(call.data.split("_")[2])
    except (IndexError, ValueError):
        return await call.answer("❌ Ошибка данных", show_alert=True)

    devices = await asyncio.to_thread(get_user_devices, user_id)
    device  = next((d for d in devices if d["id"] == dev_id), None)

    if not device:
        return await call.answer("❌ Устройство не найдено", show_alert=True)

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🗑️ Да, удалить", callback_data=f"device_delete_confirmed_{dev_id}"))
    builder.row(types.InlineKeyboardButton(text="◀️ Отмена",      callback_data=f"device_info_{dev_id}"))

    text = (
        f"⚠️ <b>Удалить устройство '{html.escape(device['device_name'])}'?</b>\n\n"
        f"Это действие отключит клиент в панели и удалит запись из БД.\n"
        f"Восстановить ключ будет невозможно."
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await call.answer()