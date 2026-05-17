import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from aiogram import Bot, types, F
from aiogram.filters import Command, BaseFilter
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.dispatcher import dp
from app.config import ADMIN_ID, PAYMENT_AMOUNT
from app.db import get_db_connection, get_user_data_dict, add_balance_atomic, save_user
from app.services.vpn import update_panel_client, activate_all_user_devices

logger = logging.getLogger(__name__)

SUBSCRIPTION_DAYS = 30

class IsAdmin(BaseFilter):
    """aiogram 3.x фильтр для элегантной проверки прав доступа на уровне роутера."""
    async def __call__(self, obj: types.Message | types.CallbackQuery) -> bool:
        return obj.from_user.id == ADMIN_ID

@dp.message(Command("give_vpn"), IsAdmin())
async def admin_give_vpn(message: types.Message, bot: Bot) -> None:
    """
    Админское продление подписки:
    1) Парсим user_id и кол-во дней.
    2) Обновляем срок в панели.
    3) Обновляем локальные данные пользователя.
    4) Начисляем баланс и при необходимости активируем устройства.
    """
    args = message.text.split()
    if len(args) < 3:
        await message.answer(
            "📝 Использование: <code>/give_vpn ID ДНИ</code>",
            parse_mode="HTML",
        )
        return

    # --- Парсинг аргументов ---
    try:
        target_id = int(args[1])
        days = int(args[2])
        if days <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Дни должны быть положительным числом.")
        return

    # --- Читаем текущие данные пользователя ---
    user_data = await asyncio.to_thread(get_user_data_dict, target_id)
    if not user_data or not user_data.get("uuid"):
        await message.answer("❌ Пользователь не найден или у него отсутствует ключ.")
        return

    old_expire = user_data["expire_at"]
    client_uuid = user_data["uuid"]
    username = user_data["username"] or f"user_{target_id}"
    vless_link = user_data.get("vless_link", "")

    now = datetime.now(timezone.utc)
    try:
        if old_expire:
            old_dt = datetime.strptime(
                old_expire,
                "%Y-%m-%d %H:%M:%S",
            ).replace(tzinfo=timezone.utc)
            start_point = max(now, old_dt)
        else:
            start_point = now
    except (ValueError, TypeError):
        logger.warning(
            "admin_give_vpn: invalid expire_at for user_id=%s value=%r",
            target_id,
            old_expire,
        )
        start_point = now

    new_expire_dt = start_point + timedelta(days=days)
    new_expire_str = new_expire_dt.strftime("%Y-%m-%d %H:%M:%S")
    target_ts_ms = int(new_expire_dt.timestamp() * 1000)

    # --- Шаг 1: панель ---
    try:
        success, err = await update_panel_client(
            client_uuid,
            target_id,
            username,
            target_ts_ms,
        )
    except Exception as e:
        logger.exception(
            "admin_give_vpn: panel update exception user_id=%s uuid=%s",
            target_id,
            client_uuid,
        )
        await message.answer(
            "❌ Критическая ошибка при обращении к панели.\n"
            f"<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    if not success:
        logger.error(
            "admin_give_vpn: panel update failed user_id=%s uuid=%s msg=%s",
            target_id,
            client_uuid,
            err,
        )
        await message.answer(
            "❌ Ошибка панели при продлении:\n"
            f"<code>{html.escape(str(err))}</code>",
            parse_mode="HTML",
        )
        return

    # --- Шаг 2: обновление локального пользователя ---
    try:
        await asyncio.to_thread(
            save_user,
            target_id,
            username,
            new_expire_str,
            vless_link,
            client_uuid,
            status="PAID",
        )
    except Exception as e:
        logger.exception(
            "admin_give_vpn: save_user failed after panel update user_id=%s uuid=%s",
            target_id,
            client_uuid,
        )
        await message.answer(
            "⚠️ Панель успешно продлена, но не удалось обновить локальные данные пользователя.\n"
            "Требуется ручная проверка.\n"
            f"<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    # --- Шаг 3: начисление баланса + возможная активация устройств ---
    # PAYMENT_AMOUNT — цена за полный период SUBSCRIPTION_DAYS в копейках
        # --- Шаг 3: начисление баланса ---
    # PAYMENT_AMOUNT — цена за полный период SUBSCRIPTION_DAYS в копейках
    daily_price_rub = (Decimal(PAYMENT_AMOUNT) / Decimal("100")) / Decimal(
        SUBSCRIPTION_DAYS,
    )
    charge_rub = (daily_price_rub * Decimal(days)).quantize(
        Decimal("0.01"),
        rounding="ROUND_HALF_UP",
    )
    amount_cents = int(charge_rub * 100)
    if amount_cents <= 0:
        amount_cents = 1

    try:
        new_balance_rub = await asyncio.to_thread(
            add_balance_atomic,
            target_id,
            amount_cents,
        )
    except Exception as e:
        logger.exception(
            "admin_give_vpn: add_balance_atomic failed user_id=%s",
            target_id,
        )
        await message.answer(
            "⚠️ Панель и срок действия обновлены, но не удалось начислить баланс.\n"
            "Требуется ручная проверка баланса.\n"
            f"<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    if new_balance_rub is None:
        logger.error(
            "admin_give_vpn: user disappeared during balance update user_id=%s",
            target_id,
        )
        await message.answer(
            "⚠️ Панель и срок действия обновлены, но пользователь исчез из БД.\n"
            "Проверьте данные вручную.",
            parse_mode="HTML",
        )
        return

    # --- Финальное уведомление ---
    await message.answer(
        f"✅ Пользователь <code>{target_id}</code> продлён до <b>{new_expire_str}</b> UTC.\n"
        f"💰 Начислено: <b>{charge_rub:.2f} руб.</b>\n"
        f"💼 Новый баланс: <b>{new_balance_rub:.2f} руб.</b>",
        parse_mode="HTML",
    )

    try:
        await bot.send_message(
            target_id,
            f"🎁 <b>Доступ продлён</b> до <b>{new_expire_str}</b> UTC администратором.",
            parse_mode="HTML",
        )
    except Exception:
        # Не критично, просто логируем
        logger.exception(
            "admin_give_vpn: failed to notify user user_id=%s",
            target_id,
        )

@dp.message(Command("send"), IsAdmin())
async def admin_send_message(message: types.Message, bot: Bot) -> None:
    """
    Отправка личного сообщения пользователю:
    /send user_id текст
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
        logger.warning(
            "admin_send_message: failed to send to user_id=%s error=%s",
            target_id,
            e,
        )
        await message.answer(
            f"❌ Ошибка отправки:\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )

@dp.message(Command("broadcast"), IsAdmin())
async def admin_broadcast(message: types.Message, bot: Bot) -> None:
    """
    Массовая рассылка сообщения всем пользователям.
    Отправляет текст, указанный после команды:
    /broadcast текст
    """
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "📝 Использование: <code>/broadcast ТЕКСТ</code>",
            parse_mode="HTML",
        )
        return

    raw_text = args[1].strip()
    if not raw_text:
        await message.answer("⚠️ Текст рассылки не может быть пустым.")
        return

    safe_text = html.escape(raw_text)

    # Получаем список пользователей
    try:
        with get_db_connection() as conn:
            rows = conn.execute("SELECT user_id FROM users").fetchall()
            user_ids = [row["user_id"] for row in rows]
    except Exception as e:
        await message.answer(
            f"❌ Ошибка БД при получении списка пользователей:\n"
            f"<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    if not user_ids:
        await message.answer("ℹ️ Нет пользователей для рассылки.")
        return

    total = len(user_ids)
    sent = 0
    errors = 0

    status_msg = await message.answer(
        f"📢 Начинаю рассылку на {total} получателей...",
        parse_mode="HTML",
    )

    text_to_send = f"📢 <b>ОБЪЯВЛЕНИЕ</b>\n\n{safe_text}"

    # Простая защита от rate limit: маленький sleep и периодические обновления статуса
    for idx, user_id in enumerate(user_ids, start=1):
        try:
            await bot.send_message(
                user_id,
                text_to_send,
                parse_mode="HTML",
            )
            sent += 1
        except Exception as e:
            errors += 1
            logger.warning(
                "admin_broadcast: failed to send to user_id=%s error=%s",
                user_id,
                e,
            )

        # Лёгкая пауза, чтобы не упереться в лимиты
        await asyncio.sleep(0.05)

        # Каждые 50 сообщений обновляем прогресс
        if idx % 50 == 0 or idx == total:
            try:
                await status_msg.edit_text(
                    f"📢 Рассылка...\n"
                    f"Всего: {total}\n"
                    f"✅ Доставлено: {sent}\n"
                    f"❌ Ошибок: {errors}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    await message.answer(
        f"✅ Рассылка завершена.\n"
        f"Всего: {total}\n"
        f"✅ Доставлено: {sent}\n"
        f"❌ Ошибок: {errors}",
        parse_mode="HTML",
    )

@dp.message(Command("stats"), IsAdmin())
async def admin_stats(message: types.Message) -> None:
    """
    Показывает базовую статистику:
    - общее количество пользователей
    - количество активных подписок (по trial_expired_at > now, трактуем как expire_at)
    """
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    try:
        with get_db_connection() as conn:
            total_row = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
            active_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM users
                WHERE trial_expired_at IS NOT NULL
                  AND trial_expired_at > ?
                """,
                (now_str,),
            ).fetchone()

        total = total_row["cnt"] if total_row else 0
        active = active_row["cnt"] if active_row else 0

        await message.answer(
            "📊 <b>СТАТИСТИКА (UTC)</b>\n"
            f"👥 Всего пользователей: <b>{total}</b>\n"
            f"⚡️ Активных подписок: <b>{active}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("admin_stats: DB error")
        await message.answer(
            "❌ Ошибка БД при получении статистики:\n"
            f"<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )

@dp.message(Command("users"), IsAdmin())
async def admin_list_users(message: types.Message) -> None:
    """
    Показывает список пользователей с простым статусов по дате trial_expired_at.
    """
    try:
        with get_db_connection() as conn:
            rows = conn.execute("""
                SELECT user_id, username, trial_expired_at
                FROM users
                ORDER BY user_id
            """).fetchall()
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
    header = "<b>👤 СПИСОК ПОЛЬЗОВАТЕЛЕЙ (UTC):</b>\n"
    text = header

    for row in rows:
        uid = row["user_id"]
        uname = row["username"]
        expire = row["trial_expired_at"]

        safe_uname = html.escape(f"@{uname}" if uname else "Скрыт")

        status = "❔"
        expire_str = "нет даты"

        if expire:
            try:
                dt = datetime.strptime(
                    expire,
                    "%Y-%m-%d %H:%M:%S",
                ).replace(tzinfo=timezone.utc)
                expire_str = expire
                status = "✅" if dt > now else "❌"
            except ValueError:
                expire_str = f"некорректная дата: {expire}"

        entry = f"{status} {safe_uname} | <code>{uid}</code> | {expire_str}\n"

        if len(text) + len(entry) > 3900:
            await message.answer(text, parse_mode="HTML")
            text = "<b>ПРОДОЛЖЕНИЕ:</b>\n"
        text += entry

    await message.answer(text, parse_mode="HTML")

@dp.message(Command("ping"), IsAdmin())
async def admin_check_panel(message: types.Message) -> None:
    wait_msg = await message.answer("🔍 Проверка соединения с панелью...")
    try:
        from app.panel_client import get_panel_session
        session = await get_panel_session()
    except Exception as e:
        logger.exception("admin_check_panel: exception in get_panel_session")
        await wait_msg.edit_text(
            "❌ <b>КРИТИЧЕСКАЯ ОШИБКА ПРИ ОБРАЩЕНИИ К ПАНЕЛИ:</b>\n"
            f"<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    if session:
        await wait_msg.edit_text(
            "✅ <b>СВЯЗЬ С ПАНЕЛЬЮ УСТАНОВЛЕНА</b>",
            parse_mode="HTML",
        )
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
        types.InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users_action")
    )
    builder.row(types.InlineKeyboardButton(text="📡 Ping", callback_data="admin_ping_action"))
    try:
        await call.message.edit_text("🕵️ <b>АДМИНИСТРАТИВНАЯ ПАНЕЛЬ</b>\nДоступные команды: /give_vpn, /send, /broadcast", parse_mode="HTML", reply_markup=builder.as_markup())
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