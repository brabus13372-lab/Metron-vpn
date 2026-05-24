import html
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Union

from aiogram import F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from app.config import SERVER_IP
from app.config import WEBAPP_URL
from app.config import ADMIN_ID, TRIAL_DAYS

from app.bot.dispatcher import dp
from app.bot.keyboards import main_kb
from app.db import (
    get_user_data_dict,
    get_user_balance,
    save_user,
)
from app.services.vpn import create_panel_client, activate_all_user_devices

logger = logging.getLogger(__name__)


async def send_dynamic_instruction(
    target: Union[types.Message, types.CallbackQuery],
    user_id: int,
) -> None:
    user = await get_user_data_dict(user_id)
    vless_link = user.get("vless_link") if user else None

    if not vless_link:
        msg = "<b>⚠️ У вас ещё нет ключа.</b>\nНажмите «🚀 Подключить VPN», чтобы получить доступ."
        if isinstance(target, types.CallbackQuery):
            await target.message.answer(msg, parse_mode="HTML")
            await target.answer()
        else:
            await target.answer(msg, parse_mode="HTML")
        return

    safe_link = html.escape(vless_link)
    text = (
        "<b>📖 ИНСТРУКЦИЯ ПО ПОДКЛЮЧЕНИЮ</b>\n"
        "――――――――――――――――――――\n\n"

        "<b>🔑 ШАГ 1 — Скопируйте ваш ключ:</b>\n"
        f"<code>{safe_link}</code>\n"
        "<i>↗️ Нажмите на ключ — он скопируется автоматически.</i>\n\n"

        "――――――――――――――――――――\n"
        "<b>📲 ШАГ 2 — Установите приложение:</b>\n\n"

        "💙 <b>iPhone / iPad (iOS):</b>\n"
        "Откройте App Store и скачайте <b>v2RayTun</b>\n\n"

        "💚 <b>Android:</b>\n"
        "Откройте Google Play и скачайте: <a href='https://play.google.com/store/apps/details?id=com.v2raytun.android'>v2RayTun</a>\n\n"

        "🖥 <b>Windows / macOS / Linux:</b>\n"
        "Скачайте с официального сайта: <a href='https://v2raytun.com'>v2raytun.com</a>\n"
        "Выберите вашу операционную систему и скачайте установщик.\n\n"

        "――――――――――――――――――――\n"
        "<b>⚙️ ШАГ 3 — Добавьте ключ в приложение:</b>\n\n"
        "1. Откройте v2RayTun\n"
        "2. Нажмите кнопку <b>[ + ]</b> в правом верхнем углу\n"
        "3. Выберите <b>« Import from Clipboard »</b>\n"
        "4. Ключ автоматически вставится — нажмите <b>« Save »</b>\n\n"

        "――――――――――――――――――――\n"
        "<b>🟢 ШАГ 4 — Включите VPN:</b>\n\n"
        "1. Выберите сохранённый конфиг (MetronVPN)\n"
        "2. Нажмите большую кнопку подключения посередине экрана\n"
        "3. При первом запуске iOS/Android запросит разрешение — нажмите <b>« Allow »</b>\n"
        "4. В статусной строке появится значок «<b>VPN</b>» — всё работает! 🎉\n\n"

        "――――――――――――――――――――\n"
        "<b>🔄 Если не работает:</b>\n"
        "• Проверьте баланс в «👤 Мой профиль»\n"
        "• Попробуйте переподключиться (выключить и включить)\n"
        "• Если не помогло — напишите в поддержку, я отвечу лично 🙌"
    )

    if isinstance(target, types.CallbackQuery):
        await target.message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
        await target.answer()
    else:
        await target.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@dp.message(Command("start"))
async def start_cmd(message: types.Message) -> None:
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"

    user = await get_user_data_dict(user_id)

    if not user:
        empty_expire = datetime.now(timezone.utc)
        await save_user(
            user_id,
            username,
            empty_expire,
            vless_link="",
            uuid_val="",
            status="NEW",
        )
        logger.info("start_cmd.new_user user_id=%s", user_id)

    name = html.escape(message.from_user.first_name)
    text = (
        f"Привет, {name}! 👋\n\n"
        "<b>MetronVPN</b> — быстрый и надёжный VPN без лишних слов.\n\n"
        "⚡️ <b>Безлимитный трафик</b> — никаких ограничений по скорости\n"
        "🔒 <b>Полная анонимность</b> — ваши данные только ваши\n"
        "🌍 <b>Обход блокировок</b> — YouTube, Instagram, любые сайты\n"
        "📱 <b>Все устройства</b> — iOS, Android, Windows, macOS, Linux\n"
        "⏱ <b>Подключение за 1 минуту</b> — просто нажмите кнопку\n\n"
        "Нажмите <b>«🚀 Подключить VPN»</b> — первые дни бесплатно!\n\n"
        "📢 Новости и обновления: <a href='https://t.me/metronVPN'>t.me/metronVPN</a>"
    )
    await message.answer(text, reply_markup=main_kb(), parse_mode="HTML", disable_web_page_preview=True)
    webapp_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🌐 Личный кабинет",
            web_app=WebAppInfo(url=f"{WEBAPP_URL}?uid={message.from_user.id}")
        )
    ]])
    await message.answer("Открой свой личный кабинет 👇", reply_markup=webapp_kb)


@dp.message(F.text == "🚀 Подключить VPN")
async def get_vpn(message: types.Message) -> None:
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    now = datetime.now(timezone.utc)

    user = await get_user_data_dict(user_id)

    if user and user.get("vless_link"):
        balance: Decimal = await get_user_balance(user_id) or Decimal(0)
        expire_raw = user.get("expire_at")
        status = user.get("status") or "NEW"

        is_expired = True
        if expire_raw:
            if isinstance(expire_raw, datetime):
                expire_dt = expire_raw
                if expire_dt.tzinfo is None:
                    expire_dt = expire_dt.replace(tzinfo=timezone.utc)
                is_expired = now > expire_dt
            else:
                logger.warning(
                    "get_vpn.unexpected_expire_type user_id=%s type=%r",
                    user_id, type(expire_raw),
                )

        has_trial_access = status == "TRIAL" and not is_expired
        has_paid_access = status == "ACTIVE" and balance > 0 and not is_expired

        if has_trial_access or has_paid_access:
            safe_link = html.escape(user["vless_link"])
            builder = InlineKeyboardBuilder()
            builder.row(types.InlineKeyboardButton(text="💳 Продлить", callback_data="buy_vpn"))
            builder.row(types.InlineKeyboardButton(text="📖 Инструкция", callback_data="show_instruction"))
            return await message.answer(
                f"✅ <b>Ваш ключ активен:</b>\n\n<code>{safe_link}</code>",
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )

        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="buy_vpn"))
        return await message.answer(
            "⚠️ <b>Доступ ограничен.</b>\nДля активации ключа необходимо пополнить баланс.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )

    wait_msg = await message.answer("⚙️ Генерируем ваш персональный ключ...")

    new_link, client_uuid, error = await create_panel_client(user_id, username)

    if not new_link:
        logger.error("get_vpn.panel_error user_id=%s err=%s", user_id, error)
        await message.bot.send_message(
            ADMIN_ID,
            f"🚨 <b>Panel Fail:</b> {user_id}\n<code>{html.escape(str(error))}</code>",
            parse_mode="HTML",
        )
        return await wait_msg.edit_text(
            "❌ Сервер перегружен. Пожалуйста, попробуйте через 5 минут.",
            parse_mode="HTML",
        )

    expire_at = now + timedelta(days=TRIAL_DAYS)
    await save_user(user_id, username, expire_at, new_link, client_uuid, status="TRIAL")

    ok, fail, _errors = await activate_all_user_devices(user_id)
    if fail > 0:
        logger.warning(
            "get_vpn.activate_partial user_id=%s ok=%s fail=%s errors=%s",
            user_id, ok, fail, _errors[:3],
        )

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="💳 Продлить (100₽)", callback_data="buy_vpn"))
    builder.row(types.InlineKeyboardButton(text="📖 Инструкция", callback_data="show_instruction"))

    await wait_msg.edit_text(
        f"✅ <b>Доступ предоставлен!</b>\n\n"
        f"🕒 Пробный период: <b>{TRIAL_DAYS} день</b>\n\n"
        f"<code>{html.escape(new_link)}</code>\n\n"
        f"<i>Нажмите на ключ, чтобы скопировать.</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    logger.info("get_vpn.trial_issued user_id=%s expire_at=%s", user_id, expire_at)


@dp.callback_query(F.data == "show_instruction")
async def handle_inline_instruction(call: types.CallbackQuery) -> None:
    await send_dynamic_instruction(call, call.from_user.id)


@dp.message(F.text == "📖 Инструкция")
async def handle_reply_instruction(message: types.Message) -> None:
    await send_dynamic_instruction(message, message.from_user.id)
