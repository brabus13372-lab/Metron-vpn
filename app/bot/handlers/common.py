import html
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Union

from aiogram import F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.dispatcher import dp
from app.bot.keyboards import main_kb
from app.config import ADMIN_ID, TRIAL_DAYS
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
        "<b>📖 ИНСТРУКЦИЯ ПО ПОДКЛЮЧЕНИЮ</b>\n\n"
        "<b>1️⃣ СКОПИРУЙТЕ ВАШ КЛЮЧ:</b>\n"
        f"<code>{safe_link}</code>\n\n"
        "<i>Нажмите на ключ выше для автокопирования.</i>\n\n"
        "<b>2️⃣ УСТАНОВИТЕ ПРИЛОЖЕНИЕ:</b>\n"
        "• <b>iOS:</b> <a href='https://apps.apple.com/app/v2raytun/id6471850124'>v2RayTun</a>\n"
        "• <b>Android:</b> <a href='https://play.google.com/store/apps/details?id=com.v2raytun.android'>v2RayTun</a>\n\n"
        "<b>3️⃣ ИМПОРТИРУЙТЕ КЛЮЧ:</b>\n"
        "• В приложении нажмите <b>[ + ]</b> -> <b>Import from Clipboard</b>.\n"
        "• Выберите конфиг и нажмите кнопку подключения.\n\n"
        "<b>🔄 ЕСЛИ НЕ РАБОТАЕТ:</b>\n"
        "• Обновите ключ в «👤 Мой профиль» или напишите в поддержку."
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

        logger.info("Created empty user record user=%s on /start", user_id)

    text = (
        f"Здравствуйте, {html.escape(message.from_user.first_name)}! 🚀\n"
        "<b>MetronVPN</b> — ваш доступ к свободному интернету.\n\n"
        "⚡️ Безлимитный трафик\n"
        "🔒 Полная анонимность\n"
        "📱 Настройка за 1 минуту\n\n"
        "Нажмите кнопку ниже, чтобы получить пробный период."
    )
    await message.answer(text, reply_markup=main_kb(), parse_mode="HTML")


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
                    "Unexpected expire_at type for user_id=%s: %r",
                    user_id,
                    type(expire_raw),
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

    new_link, client_uuid, error = await create_panel_client(user_id, username, days=TRIAL_DAYS)

    if not new_link:
        logger.error("Panel Error for %s: %s", user_id, error)
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
    await activate_all_user_devices(user_id)

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


@dp.callback_query(F.data == "show_instruction")
async def handle_inline_instruction(call: types.CallbackQuery) -> None:
    await send_dynamic_instruction(call, call.from_user.id)


@dp.message(F.text == "📖 Инструкция")
async def handle_reply_instruction(message: types.Message) -> None:
    await send_dynamic_instruction(message, message.from_user.id)