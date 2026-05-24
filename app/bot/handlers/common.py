import html
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Union

from aiogram import F, types
from aiogram.filters import Command, CommandObject
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
        msg = "<b>\u26a0\ufe0f \u0423 \u0432\u0430\u0441 \u0435\u0449\u0451 \u043d\u0435\u0442 \u043a\u043b\u044e\u0447\u0430.</b>\n\u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\ud83d\ude80 \u041f\u043e\u0434\u043a\u043b\u044e\u0447\u0438\u0442\u044c VPN\u00bb, \u0447\u0442\u043e\u0431\u044b \u043f\u043e\u043b\u0443\u0447\u0438\u0442\u044c \u0434\u043e\u0441\u0442\u0443\u043f."
        if isinstance(target, types.CallbackQuery):
            await target.message.answer(msg, parse_mode="HTML")
            await target.answer()
        else:
            await target.answer(msg, parse_mode="HTML")
        return

    safe_link = html.escape(vless_link)
    text = (
        "<b>\ud83d\udcd6 \u0418\u041d\u0421\u0422\u0420\u0423\u041a\u0426\u0418\u042f \u041f\u041e \u041f\u041e\u0414\u041a\u041b\u042e\u0427\u0415\u041d\u0418\u042e</b>\n"
        "\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\n\n"
        "<b>\ud83d\udd11 \u0428\u0410\u0413 1 \u2014 \u0421\u043a\u043e\u043f\u0438\u0440\u0443\u0439\u0442\u0435 \u0432\u0430\u0448 \u043a\u043b\u044e\u0447:</b>\n"
        f"<code>{safe_link}</code>\n"
        "<i>\u2197\ufe0f \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u043d\u0430 \u043a\u043b\u044e\u0447 \u2014 \u043e\u043d \u0441\u043a\u043e\u043f\u0438\u0440\u0443\u0435\u0442\u0441\u044f \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438.</i>\n\n"
        "\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\n"
        "<b>\ud83d\udcf2 \u0428\u0410\u0413 2 \u2014 \u0423\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u0435 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435:</b>\n\n"
        "\ud83d\udc99 <b>iPhone / iPad (iOS):</b>\n"
        "\u041e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 App Store \u0438 \u0441\u043a\u0430\u0447\u0430\u0439\u0442\u0435 <b>v2RayTun</b>\n\n"
        "\ud83d\udc9a <b>Android:</b>\n"
        "\u041e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 Google Play \u0438 \u0441\u043a\u0430\u0447\u0430\u0439\u0442\u0435: <a href='https://play.google.com/store/apps/details?id=com.v2raytun.android'>v2RayTun</a>\n\n"
        "\ud83d\udda5 <b>Windows / macOS / Linux:</b>\n"
        "\u0421\u043a\u0430\u0447\u0430\u0439\u0442\u0435 \u0441 \u043e\u0444\u0438\u0446\u0438\u0430\u043b\u044c\u043d\u043e\u0433\u043e \u0441\u0430\u0439\u0442\u0430: <a href='https://v2raytun.com'>v2raytun.com</a>\n"
        "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0432\u0430\u0448\u0443 \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u043e\u043d\u043d\u0443\u044e \u0441\u0438\u0441\u0442\u0435\u043c\u0443 \u0438 \u0441\u043a\u0430\u0447\u0430\u0439\u0442\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u0449\u0438\u043a.\n\n"
        "\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\n"
        "<b>\u2699\ufe0f \u0428\u0410\u0413 3 \u2014 \u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u043a\u043b\u044e\u0447 \u0432 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435:</b>\n\n"
        "1. \u041e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 v2RayTun\n"
        "2. \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u043a\u043d\u043e\u043f\u043a\u0443 <b>[ + ]</b> \u0432 \u043f\u0440\u0430\u0432\u043e\u043c \u0432\u0435\u0440\u0445\u043d\u0435\u043c \u0443\u0433\u043b\u0443\n"
        "3. \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 <b>\u00ab Import from Clipboard \u00bb</b>\n"
        "4. \u041a\u043b\u044e\u0447 \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438 \u0432\u0441\u0442\u0430\u0432\u0438\u0442\u0441\u044f \u2014 \u043d\u0430\u0436\u043c\u0438\u0442\u0435 <b>\u00ab Save \u00bb</b>\n\n"
        "\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\n"
        "<b>\ud83d\udfe2 \u0428\u0410\u0413 4 \u2014 \u0412\u043a\u043b\u044e\u0447\u0438\u0442\u0435 VPN:</b>\n\n"
        "1. \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0439 \u043a\u043e\u043d\u0444\u0438\u0433 (MetronVPN)\n"
        "2. \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u0431\u043e\u043b\u044c\u0448\u0443\u044e \u043a\u043d\u043e\u043f\u043a\u0443 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u044f \u043f\u043e\u0441\u0435\u0440\u0435\u0434\u0438\u043d\u0435 \u044d\u043a\u0440\u0430\u043d\u0430\n"
        "3. \u041f\u0440\u0438 \u043f\u0435\u0440\u0432\u043e\u043c \u0437\u0430\u043f\u0443\u0441\u043a\u0435 iOS/Android \u0437\u0430\u043f\u0440\u043e\u0441\u0438\u0442 \u0440\u0430\u0437\u0440\u0435\u0448\u0435\u043d\u0438\u0435 \u2014 \u043d\u0430\u0436\u043c\u0438\u0442\u0435 <b>\u00ab Allow \u00bb</b>\n"
        "4. \u0412 \u0441\u0442\u0430\u0442\u0443\u0441\u043d\u043e\u0439 \u0441\u0442\u0440\u043e\u043a\u0435 \u043f\u043e\u044f\u0432\u0438\u0442\u0441\u044f \u0437\u043d\u0430\u0447\u043e\u043a \u00ab<b>VPN</b>\u00bb \u2014 \u0432\u0441\u0451 \u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442! \ud83c\udf89\n\n"
        "\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\n"
        "<b>\ud83d\udd04 \u0415\u0441\u043b\u0438 \u043d\u0435 \u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442:</b>\n"
        "\u2022 \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u0431\u0430\u043b\u0430\u043d\u0441 \u0432 \u00ab\ud83d\udc64 \u041c\u043e\u0439 \u043f\u0440\u043e\u0444\u0438\u043b\u044c\u00bb\n"
        "\u2022 \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u0435\u0440\u0435\u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0438\u0442\u044c\u0441\u044f (\u0432\u044b\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0438 \u0432\u043a\u043b\u044e\u0447\u0438\u0442\u044c)\n"
        "\u2022 \u0415\u0441\u043b\u0438 \u043d\u0435 \u043f\u043e\u043c\u043e\u0433\u043b\u043e \u2014 \u043d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u0432 \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0443, \u044f \u043e\u0442\u0432\u0435\u0447\u0443 \u043b\u0438\u0447\u043d\u043e \ud83d"
    )

    if isinstance(target, types.CallbackQuery):
        await target.message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
        await target.answer()
    else:
        await target.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@dp.message(Command("start"))
async def start_cmd(message: types.Message, command: CommandObject) -> None:
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

    # Deep link: /start pay  — редирект из webapp на оплату
    if command.args == "pay":
        builder = InlineKeyboardBuilder()
        builder.row(
            types.InlineKeyboardButton(
                text="💳 Пополнить баланс",
                callback_data="buy_vpn",
            )
        )
        await message.answer(
            "💰 <b>Пополнение баланса</b>\n\nНажмите кнопку ниже, чтобы выбрать сумму пополнения:",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return

    name = html.escape(message.from_user.first_name)
    text = (
        f"Привет, {name}! 👋\n\n"
        "<b>MetronVPN</b> \u2014 быстрый и надёжный VPN без лишних слов.\n\n"
        "⚡️ <b>Безлимитный трафик</b> \u2014 никаких ограничений по скорости\n"
        "🔒 <b>Полная анонимность</b> \u2014 ваши данные только ваши\n"
        "🌍 <b>Обход блокировок</b> \u2014 YouTube, Instagram, любые сайты\n"
        "📱 <b>Все устройства</b> \u2014 iOS, Android, Windows, macOS, Linux\n"
        "⏱ <b>Подключение за 1 минуту</b> \u2014 просто нажмите кнопку\n\n"
        "Нажмите <b>«🚀 Подключить VPN»</b> \u2014 первые дни бесплатно!\n\n"
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
