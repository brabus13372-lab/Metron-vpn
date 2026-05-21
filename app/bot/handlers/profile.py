import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from aiogram import F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.dispatcher import dp
from app.config import ADMIN_ID, DEVICE_MONTHLY_COST, TZ_MSK, TZ_NSK
from app.db import (
    add_device,
    get_user_balance,
    get_user_data_dict,
    get_user_devices,
    remove_device,
    update_user_link,
)

from app.services.vpn import (
    add_device_to_panel,
    remove_device_from_panel,
    rotate_client_uuid,
)
from app.vless import build_vless_link

logger = logging.getLogger(__name__)

DAYS_IN_MONTH = Decimal("30")


# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------

class DeviceState(StatesGroup):
    waiting_device_name = State()


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _build_profile_kb(user_id: int) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📖 Инструкция", callback_data="show_instruction"))
    builder.row(types.InlineKeyboardButton(text="🔄 Обновить ключ", callback_data="update_my_key"))
    builder.row(types.InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="buy_vpn"))
    builder.row(types.InlineKeyboardButton(text="📱 Мои устройства", callback_data="manage_devices"))
    if user_id == ADMIN_ID:
        builder.row(types.InlineKeyboardButton(text="🛠 Административное меню", callback_data="admin_menu"))
    return builder.as_markup()


def _to_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        logger.warning("Naive datetime detected in profile helper, forcing UTC: %s", dt)
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _money(cents_or_decimal) -> Decimal:
    if isinstance(cents_or_decimal, Decimal):
        return cents_or_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return (Decimal(cents_or_decimal) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _format_dt_for_user(dt: datetime | None) -> tuple[str, str]:
    dt = _to_aware_utc(dt)
    if not dt:
        return "—", "—"
    return (
        dt.astimezone(TZ_NSK).strftime("%d.%m.%Y %H:%M"),
        dt.astimezone(TZ_MSK).strftime("%d.%m.%Y %H:%M"),
    )


def _calculate_days_left(balance_rub: Decimal, monthly_cost_rub: Decimal) -> Decimal | None:
    if monthly_cost_rub <= Decimal("0"):
        return None
    daily_cost_rub = monthly_cost_rub / DAYS_IN_MONTH
    if daily_cost_rub <= Decimal("0"):
        return None
    return (balance_rub / daily_cost_rub).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _daily_cost_from_monthly(monthly_cost: Decimal) -> Decimal:
    return (monthly_cost / Decimal("30")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _build_devices_text(devices: list[dict]) -> tuple[str, int, Decimal]:
    if not devices:
        return "\n<i>У вас пока нет добавленных устройств</i>", 0, Decimal("0.00")

    lines: list[str] = []
    active_count = 0
    total_monthly_rub = Decimal("0.00")

    for dev in devices:
        is_active = bool(dev.get("is_active", True))
        if is_active:
            active_count += 1

        monthly_cost = dev.get("monthly_cost", Decimal("0.00"))
        if not isinstance(monthly_cost, Decimal):
            monthly_cost = _money(monthly_cost)

        if is_active:
            total_monthly_rub += monthly_cost

        uuid_str = dev.get("client_uuid") or "unknown"
        uuid_display = f"{uuid_str[:8]}..." if len(uuid_str) > 8 else uuid_str

        created_at = dev.get("created_at")
        created_at = _to_aware_utc(created_at) if isinstance(created_at, datetime) else None
        created_text = created_at.astimezone(TZ_MSK).strftime("%d.%m.%Y %H:%M") if created_at else "—"

        status_icon = "🟢" if is_active else "🔴"
        status_text = "Активно" if is_active else "Отключено"

        lines.append(
            f"\n{status_icon} <b>{html.escape(dev.get('device_name') or 'Без названия')}</b>\n"
            f"   UUID: <code>{uuid_display}</code>\n"
            f"   Статус: {status_text}\n"
            f"   Стоимость: {monthly_cost:.2f} руб/мес\n"
            f"   Создано: {created_text}\n"
        )

    return "".join(lines), active_count, total_monthly_rub.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _build_access_block(
    status: str,
    expire_at: datetime | None,
    balance_rub: Decimal,
    active_devices: int,
    total_monthly_rub: Decimal,
) -> str:
    now = datetime.now(timezone.utc)
    expire_at = _to_aware_utc(expire_at)

    if status == "TRIAL":
        expiry_nsk, expiry_msk = _format_dt_for_user(expire_at)
        if expire_at and expire_at > now:
            return (
                "<b>🚀 Статус:</b> 🕓 Trial активен\n"
                "<b>📅 Trial до:</b>\n"
                f"🕒 {expiry_nsk} <b>(Новосибирск)</b>\n"
                f"🕚 {expiry_msk} <b>(Москва)</b>\n"
            )
        return (
            "<b>🚀 Статус:</b> ❌ Trial истёк\n"
            "<b>📅 Trial до:</b>\n"
            f"🕒 {expiry_nsk} <b>(Новосибирск)</b>\n"
            f"🕚 {expiry_msk} <b>(Москва)</b>\n"
        )

    if status == "ACTIVE":
        if active_devices <= 0 or total_monthly_rub <= Decimal("0"):
            return (
                "<b>🚀 Статус:</b> ✅ Активна\n"
                "<b>📊 Расход:</b> Нет активных устройств\n"
                "<b>⏳ Запас доступа:</b> Не рассчитывается\n"
            )

        days_left = _calculate_days_left(balance_rub, total_monthly_rub)
        days_left_text = f"{days_left:.1f} дн." if days_left is not None else "Не рассчитывается"
        status_text = "✅ Активна" if balance_rub > Decimal("0") else "⚠️ Отключена (Недостаточно средств)"

        return (
            f"<b>🚀 Статус:</b> {status_text}\n"
            f"<b>📊 Расход:</b> {total_monthly_rub:.2f} руб/мес\n"
            f"<b>⏳ Запас доступа:</b> {days_left_text}\n"
        )

    if status == "INACTIVE":
        return (
            "<b>🚀 Статус:</b> ⛔ Отключена\n"
            "<b>📊 Доступ:</b> Требуется пополнение баланса или повторная активация\n"
        )

    if status == "EXPIRED":
        return (
            "<b>🚀 Статус:</b> ❌ Неактивна\n"
            "<b>📊 Доступ:</b> Доступ завершён\n"
        )

    return (
        "<b>🚀 Статус:</b> ❌ Неактивна\n"
        "<b>📊 Доступ:</b> Ещё не активирован\n"
    )


async def _render_profile(user_id: int) -> tuple[str, types.InlineKeyboardMarkup]:
    user = await get_user_data_dict(user_id)
    balance = await get_user_balance(user_id) or Decimal("0.00")

    if not user:
        text = (
            "<b>⚠️ Профиль не найден.</b>\n"
            "Нажмите «🚀 Подключить VPN», чтобы создать ключ."
        )
        return text, _build_profile_kb(user_id)

    status = user.get("status") or "NEW"
    expire_at = user.get("expire_at")
    vless_link = user.get("vless_link")

    devices = await get_user_devices(user_id)
    devices_text, active_devices, total_monthly_rub = _build_devices_text(devices)

    access_block = _build_access_block(
        status=status,
        expire_at=expire_at,
        balance_rub=balance,
        active_devices=active_devices,
        total_monthly_rub=total_monthly_rub,
    )

    safe_link = html.escape(vless_link) if vless_link else "Нет ключа"

    text = (
        "<b>👤 Личный кабинет</b>\n\n"
        f"<b>🆔 ID:</b> <code>{user_id}</code>\n"
        f"<b>💰 Баланс:</b> <b>{balance:.2f} руб.</b>\n\n"
        f"{access_block}\n"
        f"<b>🔑 Основной ключ:</b>\n<code>{safe_link}</code>\n\n"
        f"<b>📱 Устройства:</b>{devices_text}\n\n"
        "<i>💡 Если VPN перестал подключаться, попробуйте обновить основной ключ.</i>"
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
    user = await get_user_data_dict(user_id)

    if not user or not user.get("uuid"):
        await call.answer("❌ У вас ещё нет ключа для обновления.", show_alert=True)
        return

    old_uuid = user["uuid"]
    clean_username = user.get("username") or call.from_user.username or f"user_{user_id}"

    success, new_uuid, err = await rotate_client_uuid(
        old_uuid,
        user_id,
        clean_username,
    )

    if not success or not new_uuid:
        logger.error("rotate_key_failed user_id=%s err=%s", user_id, err)
        await call.answer("❌ Ошибка обновления ключа. Сервер недоступен.", show_alert=True)
        return

    link = build_vless_link(new_uuid, clean_username)
    await update_user_link(user_id, link, new_uuid)

    logger.info("key_rotated_successfully user_id=%s", user_id)

    await call.message.answer(
        "<b>✅ Ключ успешно обновлён!</b>\n\n"
        "Предыдущий ключ деактивирован.\n"
        f"Ваш новый ключ:\n<code>{html.escape(link)}</code>",
        parse_mode="HTML",
    )
    await call.answer("Готово! 🚀")


# ---------------------------------------------------------------------------
# Управление устройствами — меню
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "manage_devices")
async def manage_devices_menu(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id

    balance = await get_user_balance(user_id) or Decimal("0.00")
    devices = await get_user_devices(user_id)

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Добавить устройство", callback_data="device_add"))

    active_count = 0
    total_monthly = Decimal("0.00")

    for dev in devices:
        is_active = bool(dev.get("is_active", True))
        if is_active:
            active_count += 1
            mc = dev.get("monthly_cost", Decimal("0.00"))
            total_monthly += mc if isinstance(mc, Decimal) else _money(mc)

        status_icon = "🟢" if is_active else "🔴"
        status_suffix = "активно" if is_active else "выключено"
        mc = dev.get("monthly_cost", Decimal("0.00"))

        builder.row(
            types.InlineKeyboardButton(
                text=(
                    f"{status_icon} {dev['device_name']} "
                    f"({mc:.2f}р/мес, {status_suffix})"
                ),
                callback_data=f"device_info_{dev['id']}",
            )
        )

    builder.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_profile"))

    if active_count > 0:
        summary_block = (
            f"<b>🟢 Активных устройств:</b> {active_count}\n"
            f"<b>📊 Общий расход:</b> {total_monthly:.2f} руб/мес\n"
        )
    else:
        summary_block = (
            "<b>🟢 Активных устройств:</b> 0\n"
            "<b>📊 Общий расход:</b> 0.00 руб/мес\n"
        )

    text = (
        "<b>📱 Управление устройствами</b>\n\n"
        f"<b>💰 Текущий баланс:</b> {balance:.2f} руб.\n"
        f"{summary_block}\n"
        "<i>Каждое устройство — отдельный клиент в VPN. "
        "Списание идёт только по активным устройствам.</i>"
    )

    await call.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await call.answer()


# ---------------------------------------------------------------------------
# Добавление устройства (FSM)
# ---------------------------------------------------------------------------

def _device_add_cancel_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="◀️ Отмена", callback_data="manage_devices"))
    return builder.as_markup()


def _device_connect_first_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="buy_vpn"))
    builder.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="manage_devices"))
    return builder.as_markup()


def _device_added_kb() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="◀️ К устройствам", callback_data="manage_devices"))
    return builder.as_markup()


def _can_add_device(user: dict | None, balance: Decimal) -> tuple[bool, str | None]:
    if not user or not user.get("uuid") or not user.get("vless_link"):
        return False, (
            "<b>📱 Сначала подключите VPN</b>\n\n"
            "Чтобы добавить устройство, сначала нужно создать основной VPN-ключ."
        )

    status = user.get("status") or "NEW"
    if status not in {"TRIAL", "ACTIVE"}:
        return False, (
            "<b>📱 Добавление устройства недоступно</b>\n\n"
            "Сначала активируйте доступ, затем можно будет добавить отдельные устройства."
        )

    if balance <= Decimal("0"):
        return False, (
            "<b>💰 Недостаточно средств</b>\n\n"
            "На балансе нет средств для добавления нового устройства. Пополните баланс и попробуйте снова."
        )

    return True, None


@dp.callback_query(F.data == "device_add")
async def device_add_start(call: types.CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id

    user = await get_user_data_dict(user_id)
    balance = await get_user_balance(user_id) or Decimal("0.00")

    allowed, error_text = _can_add_device(user, balance)
    if not allowed:
        await state.clear()
        await call.message.edit_text(
            error_text, parse_mode="HTML", reply_markup=_device_connect_first_kb()
        )
        await call.answer()
        return

    monthly_cost = DEVICE_MONTHLY_COST
    daily_cost = _daily_cost_from_monthly(monthly_cost)

    await state.set_state(DeviceState.waiting_device_name)

    text = (
        "<b>➕ Добавление нового устройства</b>\n\n"
        f"<b>💰 Баланс:</b> {balance:.2f} руб.\n"
        f"<b>💳 Стоимость устройства:</b> {monthly_cost:.2f} руб/мес "
        f"(~{daily_cost:.2f} руб/день)\n\n"
        "Введите название устройства, например: <i>iPhone</i>, <i>Ноутбук</i>.\n\n"
        "<i>Для отмены нажмите кнопку ниже или отправьте /cancel</i>"
    )

    await call.message.edit_text(text, parse_mode="HTML", reply_markup=_device_add_cancel_kb())
    await call.answer()


@dp.message(StateFilter(DeviceState.waiting_device_name), F.text == "/cancel")
async def device_add_cancel(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Добавление устройства отменено.")


@dp.message(StateFilter(DeviceState.waiting_device_name), F.text)
async def device_add_confirm(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    clean_device_name = (message.text or "").strip()

    if not clean_device_name or len(clean_device_name) > 50:
        await message.answer("❌ Название устройства должно быть от 1 до 50 символов.")
        return

    user = await get_user_data_dict(user_id)
    balance = await get_user_balance(user_id) or Decimal("0.00")

    allowed, error_text = _can_add_device(user, balance)
    if not allowed:
        await state.clear()
        await message.answer(error_text, parse_mode="HTML")
        return

    clean_username = user.get("username") or message.from_user.username or f"user_{user_id}"

    vless_link = None
    client_uuid = None

    try:
        vless_link, client_uuid, error = await add_device_to_panel(
            user_id=user_id,
            username=clean_username,
            device_name=clean_device_name,
        )

        if error or not vless_link or not client_uuid:
            logger.warning(
                "device_add_panel_failed user_id=%s device_name=%r error=%r",
                user_id, clean_device_name, error,
            )
            await state.clear()
            await message.answer(
                "❌ Не удалось создать устройство: панель недоступна или вернула ошибку.\n"
                "Попробуйте позже."
            )
            return

        monthly_cost_cents = int(
            (DEVICE_MONTHLY_COST * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP)
        )

        dev_id = await add_device(
            user_id=user_id,
            device_name=clean_device_name,
            client_uuid=client_uuid,
            vless_link=vless_link,
            monthly_cost_cents=monthly_cost_cents,
        )

        if not dev_id:
            raise RuntimeError("Database returned empty device id")

    except Exception as e:
        logger.exception(
            "device_add_failed user_id=%s device_name=%r err=%s",
            user_id, clean_device_name, e,
        )

        if client_uuid:
            try:
                await remove_device_from_panel(client_uuid, user_id)
            except Exception:
                logger.exception(
                    "device_add_rollback_failed user_id=%s client_uuid=%s",
                    user_id, client_uuid,
                )

        await state.clear()
        await message.answer("❌ Ошибка добавления устройства. Попробуйте позже.")
        return

    await state.clear()

    logger.info(
        "device_added_successfully user_id=%s device_id=%s device_name=%r",
        user_id, dev_id, clean_device_name,
    )

    monthly_cost = DEVICE_MONTHLY_COST
    daily_cost = _daily_cost_from_monthly(monthly_cost)

    await message.answer(
        "✅ <b>Устройство успешно добавлено!</b>\n\n"
        f"<b>📱 Название:</b> {html.escape(clean_device_name)}\n"
        f"<b>💰 Стоимость:</b> {monthly_cost:.2f} руб/мес (~{daily_cost:.2f} руб/день)\n"
        f"<b>🔑 Ключ устройства:</b>\n<code>{html.escape(vless_link)}</code>\n\n"
        "<i>Списания будут идти только пока устройство активно.</i>",
        parse_mode="HTML",
        reply_markup=_device_added_kb(),
    )


# ---------------------------------------------------------------------------
# Информация об устройстве
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("device_info_"))
async def device_info(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    try:
        # "device_info_123" → split("_", 2) → ["device", "info", "123"]
        dev_id = int(call.data.split("_", 2)[2])
    except (IndexError, ValueError):
        return await call.answer("❌ Ошибка данных", show_alert=True)

    devices = await get_user_devices(user_id)
    device = next((d for d in devices if d["id"] == dev_id), None)

    if not device:
        return await call.answer("❌ Устройство не найдено", show_alert=True)

    monthly_cost = device["monthly_cost"]
    daily_cost = _daily_cost_from_monthly(monthly_cost)

    created_at = device.get("created_at")
    created_at = _to_aware_utc(created_at) if isinstance(created_at, datetime) else None
    created_text = created_at.astimezone(TZ_MSK).strftime("%d.%m.%Y %H:%M") if created_at else "—"

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🗑️ Удалить устройство", callback_data=f"device_delete_{dev_id}"))
    builder.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="manage_devices"))

    text = (
        f"<b>📱 Устройство: {html.escape(device['device_name'])}</b>\n\n"
        f"<b>🆔 UUID:</b> <code>{device['client_uuid']}</code>\n"
        f"<b>💰 Стоимость:</b> {monthly_cost:.2f} руб/мес (~{daily_cost:.2f} руб/день)\n"
        f"<b>📅 Создано:</b> {created_text}\n\n"
        f"<b>🔑 Ключ:</b>\n<code>{html.escape(device['vless_link'] or 'Нет')}</code>\n\n"
        "<i>При удалении устройство будет отключено.</i>"
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await call.answer()


# ---------------------------------------------------------------------------
# Удаление устройства — подтверждение (ПЕРВЫМ, до общего prefix)
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("device_delete_confirmed_"))
async def device_delete_final(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    try:
        # "device_delete_confirmed_123" → split("_", 3) → ["device", "delete", "confirmed", "123"]
        dev_id = int(call.data.split("_", 3)[3])
    except (IndexError, ValueError):
        return await call.answer("❌ Ошибка данных", show_alert=True)

    devices = await get_user_devices(user_id)
    device = next((d for d in devices if d["id"] == dev_id), None)

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

    removed = await remove_device(dev_id, user_id)

    if removed:
        logger.info("Device '%s' (ID: %s) deleted for user %s", device["device_name"], dev_id, user_id)
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="manage_devices"))
        await call.message.edit_text(
            f"✅ <b>Устройство '{html.escape(device['device_name'])}' удалено</b>\n\n"
            "Клиент деактивирован, запись удалена.",
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
        # "device_delete_123" → split("_", 2) → ["device", "delete", "123"]
        dev_id = int(call.data.split("_", 2)[2])
    except (IndexError, ValueError):
        return await call.answer("❌ Ошибка данных", show_alert=True)

    devices = await get_user_devices(user_id)
    device = next((d for d in devices if d["id"] == dev_id), None)

    if not device:
        return await call.answer("❌ Устройство не найдено", show_alert=True)

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🗑️ Да, удалить", callback_data=f"device_delete_confirmed_{dev_id}"))
    builder.row(types.InlineKeyboardButton(text="◀️ Отмена", callback_data=f"device_info_{dev_id}"))

    text = (
        f"⚠️ <b>Удалить устройство '{html.escape(device['device_name'])}'?</b>\n\n"
        "Это действие отключит клиент в панели и удалит запись из БД.\n"
        "Восстановить ключ будет невозможно."
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await call.answer()