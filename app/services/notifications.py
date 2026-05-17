import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.db import get_db_connection


# --- ПРОВЕРКА СРОКОВ ---
async def check_expirations(bot: Bot) -> None:
    """
    Уведомляет пользователей, у которых общий доступ по аккаунту
    закончится в ближайший час. Учитывает количество устройств и
    суммарную месячную стоимость по devices.
    """
    try:
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        soon_str = (now + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

        with get_db_connection(commit=True) as conn:
            # Берём пользователей, у которых срок в течение часа, и подтягиваем агрегаты по устройствам
            rows = conn.execute(
                """
                SELECT
                    u.user_id,
                    u.trial_expired_at,
                    COUNT(d.id)                              AS device_count,
                    COALESCE(SUM(d.monthly_cost), 0)         AS devices_monthly_cost
                FROM users u
                LEFT JOIN devices d ON d.user_id = u.user_id
                WHERE u.notified = 0
                  AND u.trial_expired_at IS NOT NULL
                  AND u.trial_expired_at > ?
                  AND u.trial_expired_at <= ?
                GROUP BY u.user_id, u.trial_expired_at
                """,
                (now_str, soon_str),
            ).fetchall()

            for row in rows:
                user_id = row["user_id"]
                device_count = row["device_count"] or 0
                devices_monthly = row["devices_monthly_cost"] or 0

                # Строка про устройства
                if device_count <= 0:
                    devices_line = (
                        "\nУ вас пока нет добавленных устройств. "
                        "После продления вы сможете подключить их в разделе «Мои устройства»."
                    )
                else:
                    devices_line = (
                        f"\nСейчас у вас подключено устройств: <b>{device_count}</b>, "
                        f"общая стоимость: <b>{devices_monthly:.2f} руб/мес</b>."
                    )

                kb = [
                    [InlineKeyboardButton(text="💳 Продлить доступ (100₽)", callback_data="buy_vpn")],
                    [InlineKeyboardButton(text="📖 Инструкция", callback_data="show_instruction")],
                ]
                pay_markup = InlineKeyboardMarkup(inline_keyboard=kb)

                try:
                    await bot.send_message(
                        user_id,
                        "⚠️ <b>Ваш доступ скоро закончится.</b>\n\n"
                        "Через 60 минут MetronVPN будет отключён для всех устройств, "
                        "привязанных к этому аккаунту. "
                        "Чтобы сохранить скорость соединения, продлите подписку, нажав кнопку ниже."
                        f"{devices_line}",
                        reply_markup=pay_markup,
                        parse_mode="HTML",
                    )

                    # Помечаем как уведомлённого
                    conn.execute(
                        "UPDATE users SET notified = 1 WHERE user_id = ?",
                        (user_id,),
                    )

                except Exception as e:
                    logging.error("❌ Ошибка уведомления пользователя %s: %s", user_id, e)

            # Сбрасываем флажок notified, когда срок уже прошёл
            conn.execute(
                """
                UPDATE users
                SET notified = 0
                WHERE notified = 1
                  AND trial_expired_at < ?
                """,
                (now_str,),
            )

    except Exception as e:
        logging.error("❌ Ошибка проверки сроков: %s", e)