import asyncio
import html
import logging
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import Bot, types, F
from aiogram.filters import Command, BaseFilter
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.vpn import activate_all_user_devices
from app.bot.dispatcher import dp
from app.config import ADMIN_ID
from app.db import (
    get_db,
    get_user_data_dict,
    add_balance_atomic,
    get_user_balance,
    update_user_status,
    get_user_devices,
)

logger = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    """aiogram 3.x фильтр для проверки прав доступа."""
    async def __call__(self, obj: types.Message | types.CallbackQuery) -> bool:
        return obj.from_user.id == ADMIN_ID


@dp.message(Command("give_balance"), IsAdmin())
async def admin_give_balance(message: types.Message, bot: Bot) -> None:
    """
    Пополнение баланса пользователя администратором.
    /give_balance ID СУММА_РУБЛЕЙ
    """
    args = message.text.split()
    if len(args) < 3:
        await message.answer(
            "📝 Использование: <code>/give_balance ID СУММА_РУБЛЕЙ</code>\n"
            "Пример: <code>/give_balance 123456789 100</code>",
            parse_mode="HTML",
        )
        return

    try:
        target_id = int(args[1])
        amount_rub = Decimal(args[2])
        if amount_rub <= 0:
            raise ValueError
    except (ValueError, Exception):
        await message.answer("❌ Сумма должна быть положительным числом.")
        return

    amount_cents = int(amount_rub * 100)

    user_data = await get_user_data_dict(target_id)
    if not user_data:
        await message.answer("❌ Пользователь не найден.")
        return

    try:
        new_balance_rub = await add_balance_atomic(
            target_id,
            amount_cents,
            reference_type="admin_give_balance",
            reference_id=str(message.from_user.id),
        )
    except Exception as e:
        logger.exception("admin_give_balance: add_balance_atomic failed user_id=%s", target_id)
        await message.answer(
            f"❌ Ошибка при начислении баланса:\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    if new_balance_rub is None:
        await message.answer("❌ Пользователь исчез из БД во время операции.")
        return

    await message.answer(
        f"✅ Пользователю <code>{target_id}</code> начислено <b>{amount_rub:.2f} руб.</b>\n"
        f"💼 Новый баланс: <b>{new_balance_rub:.2f} руб.</b>",
        parse_mode="HTML",
    )

    # Реактивируем устройства если были деактивированы из-за баланса
    devices = await get_user_devices(target_id)
    inactive = [d for d in devices if not d["is_active"] and d.get("disabled_reason") == "insufficient_funds"]

    if inactive:
        success_count, fail_count, _ = await activate_all_user_devices(target_id)
        async with get_db().connection() as conn:
            await conn.execute(
                """UPDATE devices SET is_active = true, disabled_at = NULL, disabled_reason = NULL
                WHERE user_id = $1 AND disabled_reason = 'insufficient_funds'""",
                target_id,
            )
        logger.info(
            "admin_give_balance: reactivated devices user_id=%s ok=%s fail=%s",
            target_id, success_count, fail_count,
        )
        await message.answer(
            f"🔌 Устройства пользователя <code>{target_id}</code> реактивированы: "
            f"✅{success_count} ❌{fail_count}",
            parse_mode="HTML",
        )

    try:
        await bot.send_message(
            target_id,
            f"💰 Администратор пополнил ваш баланс на <b>{amount_rub:.2f} руб.</b>\n"
            f"💼 Текущий баланс: <b>{new_balance_rub:.2f} руб.</b>",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("admin_give_balance: failed to notify user_id=%s", target_id)


@dp.message(Command("send"), IsAdmin())
async def admin_send_message(message: types.Message, bot: Bot) -> None:
    """
    Отправка личного сообщения пользователю.
    /send ID ТЕКСТ
    """
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "📝 Использование: <code>/send ID ТЕКСТ</code>",
            parse_mode="HTML",
        )
        return

    try:
        target_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID должен быть целым числом.")
        return

    safe_text = html.escape(args[2].strip())
    if not safe_text:
        await message.answer("⚠️ Текст сообщения не может быть пустым.")
        return

    try:
        await bot.send_message(
            target_id,
            f"📩 <b>Сообщение от администратора:</b>\n\n{safe_text}",
            parse_mode="HTML",
        )
        await message.answer("✅ Отправлено")
    except Exception as e:
        logger.warning("admin_send_message: failed to send to user_id=%s error=%s", target_id, e)
        await message.answer(
            f"❌ Ошибка отправки:\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )


@dp.message(Command("broadcast"), IsAdmin())
async def admin_broadcast(message: types.Message, bot: Bot) -> None:
    """
    Массовая рассылка всем пользователям.
    /broadcast ТЕКСТ
    """
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer(
            "📝 Использование: <code>/broadcast ТЕКСТ</code>",
            parse_mode="HTML",
        )
        return

    safe_text = html.escape(args[1].strip())

    try:
        db = get_db()
        async with db.connection() as conn:
            rows = await conn.fetch("SELECT user_id FROM users")
            user_ids = [row["user_id"] for row in rows]
    except Exception as e:
        await message.answer(
            f"❌ Ошибка БД:\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    if not user_ids:
        await message.answer("ℹ️ Нет пользователей для рассылки.")
        return

    total = len(user_ids)
    sent = 0
    errors = 0
    text_to_send = f"📢 <b>ОБЪЯВЛЕНИЕ</b>\n\n{safe_text}"

    status_msg = await message.answer(
        f"📢 Начинаю рассылку на {total} получателей...",
        parse_mode="HTML",
    )

    for idx, user_id in enumerate(user_ids, start=1):
        try:
            await bot.send_message(user_id, text_to_send, parse_mode="HTML")
            sent += 1
        except Exception as e:
            errors += 1
            logger.warning("admin_broadcast: failed uid=%s err=%s", user_id, e)

        await asyncio.sleep(0.05)

        if idx % 50 == 0 or idx == total:
            try:
                await status_msg.edit_text(
                    f"📢 Рассылка...\nВсего: {total}\n✅ Доставлено: {sent}\n❌ Ошибок: {errors}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    await message.answer(
        f"✅ Рассылка завершена.\nВсего: {total}\n✅ Доставлено: {sent}\n❌ Ошибок: {errors}",
        parse_mode="HTML",
    )


@dp.message(Command("stats"), IsAdmin())
async def admin_stats(message: types.Message) -> None:
    """Базовая статистика по пользователям."""
    try:
        db = get_db()
        async with db.connection() as conn:
            total_row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM users")
            active_row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM users WHERE status = 'ACTIVE'"
            )
            trial_row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM users WHERE status = 'TRIAL'"
            )
            devices_row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM devices WHERE is_active = TRUE"
            )

        await message.answer(
            "📊 <b>СТАТИСТИКА</b>\n"
            f"👥 Всего пользователей: <b>{total_row['cnt']}</b>\n"
            f"⚡️ Активных (ACTIVE): <b>{active_row['cnt']}</b>\n"
            f"🆓 На триале (TRIAL): <b>{trial_row['cnt']}</b>\n"
            f"📱 Активных устройств: <b>{devices_row['cnt']}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("admin_stats: DB error")
        await message.answer(
            f"❌ Ошибка БД:\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )


@dp.message(Command("users"), IsAdmin())
async def admin_list_users(message: types.Message) -> None:
    """Список пользователей со статусом и балансом."""
    try:
        db = get_db()
        async with db.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, username, status, balance, expire_at
                FROM users
                ORDER BY user_id
                """
            )
    except Exception as e:
        logger.exception("admin_list_users: DB error")
        await message.answer(
            f"❌ Ошибка БД:\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    if not rows:
        await message.answer("Список пуст.")
        return

    now = datetime.now(timezone.utc)
    text = "<b>👤 ПОЛЬЗОВАТЕЛИ:</b>\n"

    STATUS_EMOJI = {
        "ACTIVE": "🟢",
        "TRIAL": "🆓",
        "EXPIRED": "🔴",
        "INACTIVE": "⚫",
        "NEW": "🆕",
    }

    for row in rows:
        uid = row["user_id"]
        uname = html.escape(f"@{row['username']}" if row["username"] else "Скрыт")
        status = row["status"]
        balance_rub = Decimal(row["balance"]) / 100
        expire = row["expire_at"]
        emoji = STATUS_EMOJI.get(status, "❔")

        # expire_at показываем только для триала
        extra = ""
        if status == "TRIAL" and expire:
            if expire.tzinfo is None:
                expire = expire.replace(tzinfo=timezone.utc)
            extra = f" до {expire.strftime('%d.%m %H:%M')}"

        entry = (
            f"{emoji} {uname} | <code>{uid}</code> | "
            f"{status}{extra} | 💰{balance_rub:.2f}р\n"
        )

        if len(text) + len(entry) > 3900:
            await message.answer(text, parse_mode="HTML")
            text = "<b>ПРОДОЛЖЕНИЕ:</b>\n"
        text += entry

    await message.answer(text, parse_mode="HTML")


@dp.message(Command("ping"), IsAdmin())
async def admin_check_panel(message: types.Message) -> None:
    """Проверка соединения с панелью."""
    wait_msg = await message.answer("🔍 Проверка соединения с панелью...")
    try:
        from app.core.panel_client import get_panel_session
        session = await get_panel_session()
    except Exception as e:
        logger.exception("admin_check_panel: exception")
        await wait_msg.edit_text(
            f"❌ <b>КРИТИЧЕСКАЯ ОШИБКА:</b>\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    if session:
        await wait_msg.edit_text("✅ <b>СВЯЗЬ С ПАНЕЛЬЮ УСТАНОВЛЕНА</b>", parse_mode="HTML")
    else:
        await wait_msg.edit_text(
            "❌ <b>ОШИБКА!</b> Панель недоступна или неверные учётные данные.",
            parse_mode="HTML",
        )


@dp.callback_query(F.data == "admin_menu", IsAdmin())
async def show_admin_commands(call: types.CallbackQuery) -> None:
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats_action"),
        types.InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users_action"),
    )
    builder.row(types.InlineKeyboardButton(text="📡 Ping", callback_data="admin_ping_action"))
    try:
        await call.message.edit_text(
            "🕵️ <b>АДМИНИСТРАТИВНАЯ ПАНЕЛЬ</b>\n"
            "Доступные команды: /give_balance, /send, /broadcast",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
    except Exception:
        pass
    await call.answer()


@dp.callback_query(F.data == "admin_stats_action", IsAdmin())
async def btn_stats(call: types.CallbackQuery) -> None:
    await admin_stats(call.message)
    await call.answer()


@dp.callback_query(F.data == "admin_ping_action", IsAdmin())
async def btn_ping(call: types.CallbackQuery) -> None:
    await admin_check_panel(call.message)
    await call.answer()


@dp.callback_query(F.data == "admin_users_action", IsAdmin())
async def btn_users(call: types.CallbackQuery) -> None:
    await admin_list_users(call.message)
    await call.answer()