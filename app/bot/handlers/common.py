import html
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Union

from aiogram import F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import WEBAPP_URL, ADMIN_ID, TRIAL_DAYS
from app.bot.dispatcher import dp
from app.bot.keyboards import main_kb, webapp_button
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
        msg = (
            "<b>⚠️ У вас ещё нет ключа.</b>\n"
            "Откройте личный кабинет и нажмите «🚀 Подключить VPN», чтобы получить доступ."
        )
        if isinstance(target, types.CallbackQuery):
            await target.message.answer(msg, parse_mode="HTML")
            await target.answer()
        else:
            await target.answer(msg, parse_mode="HTML")
        return

    safe_link = html.escape(vless_link)
    text = (
        "<b>📖 ИНСТРУКЦИЯ ПО ПОДКЛЮЧЕНИЮ</b>\n"
        "――――――――――――――――――――――\n\n"
        "<b>🔑 ШАГ 1 — Скопируйте ваш ключ:</b>\n"
        f"<code>{safe_link}</code>\n"
        "<i>↗️ Нажмите на ключ — он скопируется автоматически.</i>\n\n"
        "――――――――――――――――――――――\n"
        "<b>📲 ШАГ 2 — Установите приложение:</b>\n\n"
        "💙 <b>iPhone / iPad (iOS):</b>\n"
        "Откройте App Store и скачайте <b>v2RayTun</b>\n\n"
        "💚 <b>Android:</b>\n"
        "Откройте Google Play и скачайте: <a href='https://play.google.com/store/apps/details?id=com.v2raytun.android'>v2RayTun</a>\n\n"
        "🖥 <b>Windows / macOS / Linux:</b>\n"
        "Скачайте с официального сайта: <a href='https://v2raytun.com'>v2raytun.com</a>\n"
        "Выберите вашу операционную систему и скачайте установщик.\n\n"
        "――――――――――――――――――――――\n"
        "<b>⚙️ ШАГ 3 — Добавьте ключ в приложение:</b>\n\n"
        "1. Откройте v2RayTun\n"
        "2. Нажмите кнопку <b>[ + ]</b> в правом верхнем углу\n"
        "3. Выберите <b>«Import from Clipboard»</b>\n"
        "4. Ключ автоматически вставится — нажмите <b>«Save»</b>\n\n"
        "――――――――――――――――――――――\n"
        "<b>🟢 ШАГ 4 — Включите VPN:</b>\n\n"
        "1. Выберите сохранённый конфиг (MetronVPN)\n"
        "2. Нажмите большую кнопку подключения посередине экрана\n"
        "3. При первом запуске iOS/Android запросит разрешение — нажмите <b>«Allow»</b>\n"
        "4. В статусной строке появится значок «<b>VPN</b>» — всё работает! 🎉\n\n"
        "――――――――――――――――――――――\n"
        "<b>🔄 Если не работает:</b>\n"
        "• Проверьте баланс в личном кабинете\n"
        "• Попробуйте переподключиться (выключить и включить)\n"
        "• Если не помогло — напишите в поддержку, я отвечу лично 👇"
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

    # Deep link: /start pay — сразу показываем инлайн-кнопки с суммами
    if command.args in ("pay", "topup"):
        try:
            from app.config import PAYMENT_AMOUNTS
            amounts = PAYMENT_AMOUNTS
        except ImportError:
            amounts = [10000, 20000, 50000, 100000]

        builder = InlineKeyboardBuilder()
        for amount_cents in amounts:
            rub = amount_cents // 100
            builder.row(
                types.InlineKeyboardButton(
                    text=f"💳 Пополнить на {rub} руб.",
                    callback_data=f"pay_amount_{amount_cents}",
                )
            )

        await message.answer(
            "💰 <b>Пополнение баланса</b>\n\nВыберите сумму:",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return

    name = html.escape(message.from_user.first_name)
    text = (
        f"Привет, {name}! 👋\n\n"
        "<b>MetronVPN</b> — быстрый и надёжный VPN без лишних слов.\n\n"
        "⚡️ <b>Безлимитный трафик</b> — никаких ограничений по скорости\n"
        "🔒 <b>Полная анонимность</b> — ваши данные только ваши\n"
        "🌍 <b>Обход блокировок</b> — YouTube, Instagram, любые сайты\n"
        "📱 <b>Все устройства</b> — iOS, Android, Windows, macOS, Linux\n"
        "⏱ <b>Подключение за 1 минуту</b> — просто нажмите кнопку\n\n"
        "Нажмите <b>«🌐 Личный кабинет»</b> — всё управление там!\n\n"
        "📢 Новости и обновления: <a href='https://t.me/metronVPN'>t.me/metronVPN</a>"
    )
    await message.answer(
        text,
        reply_markup=main_kb(WEBAPP_URL, user_id),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data == "show_instruction")
async def handle_inline_instruction(call: types.CallbackQuery) -> None:
    await send_dynamic_instruction(call, call.from_user.id)


@dp.message(F.text == "📖 Инструкция")
async def handle_reply_instruction(message: types.Message) -> None:
    """Оставляем обратную совместимость для старых reply-кнопок у существующих юзеров."""
    await send_dynamic_instruction(message, message.from_user.id)
