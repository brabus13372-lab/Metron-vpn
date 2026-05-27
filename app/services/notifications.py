import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from aiogram import Bot
from app.bot.keyboards import webapp_button
from app.config import WEBAPP_URL
from app.db import get_db

logger = logging.getLogger(__name__)

# Уведомление за 2 дня до исчерпания баланса (повторное — критическое)
LOW_BALANCE_DAYS_THRESHOLD = Decimal("2")
# Уведомление за 5 дней (первое — раннее предупреждение)
LOW_BALANCE_DAYS_THRESHOLD_EARLY = Decimal("5")
DAYS_IN_MONTH = Decimal("30")


def _cents_to_decimal(cents: int) -> Decimal:
    return (Decimal(cents) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _rub_text(cents: int) -> str:
    return f"{_cents_to_decimal(cents):.2f}"


def _days_left(balance_cents: int, monthly_cost_cents: int) -> Decimal:
    if balance_cents <= 0 or monthly_cost_cents <= 0:
        return Decimal("0")
    daily_cost = Decimal(monthly_cost_cents) / DAYS_IN_MONTH
    if daily_cost <= 0:
        return Decimal("0")
    return (Decimal(balance_cents) / daily_cost).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _trial_devices_line(device_count: int, devices_monthly_cents: int) -> str:
    if device_count <= 0:
        return (
            "\nУ вас пока нет добавленных устройств. "
            "После пополнения баланса вы сможете подключить их в разделе «Мои устройства»."
        )
    return (
        f"\nСейчас у вас подключено устройств: <b>{device_count}</b>, "
        f"общая стоимость активных устройств: <b>{_rub_text(devices_monthly_cents)} руб/мес</b>."
    )


def _build_trial_message(device_count: int, devices_monthly_cents: int) -> str:
    return (
        "⚠️ <b>Ваш trial скоро закончится.</b>\n\n"
        "Через 60 минут пробный доступ MetronVPN завершится. "
        "Чтобы сохранить доступ, пополните баланс кнопкой ниже."
        f"{_trial_devices_line(device_count, devices_monthly_cents)}"
    )


def _build_low_balance_message(
    balance_cents: int,
    device_count: int,
    monthly_cost_cents: int,
    days_left: Decimal,
    *,
    critical: bool = False,
) -> str:
    title = (
        "🚨 <b>Баланс почти исчерпан.</b>"
        if critical else
        "⚠️ <b>Баланс скоро закончится.</b>"
    )
    action = (
        "Пополните баланс как можно скорее, чтобы доступ не отключился."
        if critical else
        "Чтобы доступ не отключился, пополните баланс заранее."
    )
    return (
        f"{title}\n\n"
        f"Текущий баланс: <b>{_rub_text(balance_cents)} ₽</b>\n"
        f"Активных устройств: <b>{device_count}</b>\n"
        f"Текущий расход: <b>{_rub_text(monthly_cost_cents)} руб/мес</b>\n"
        f"Остатка хватит примерно на <b>{days_left:.1f}</b> дн.\n\n"
        f"{action}"
    )


def _build_zero_balance_message(device_count: int, monthly_cost_cents: int) -> str:
    return (
        "❌ <b>Баланс исчерпан — VPN отключён.</b>\n\n"
        f"Активных устройств: <b>{device_count}</b>\n"
        f"Стоимость: <b>{_rub_text(monthly_cost_cents)} руб/мес</b>\n\n"
        "Все ваши ключи приостановлены. Пополните баланс — доступ восстановится автоматически."
    )


def _build_reactivated_message(device_count: int, balance_cents: int, days_left: Decimal) -> str:
    return (
        "✅ <b>VPN снова активен!</b>\n\n"
        f"Баланс пополнен: <b>{_rub_text(balance_cents)} ₽</b>\n"
        f"Активных устройств: <b>{device_count}</b>\n"
        f"Доступа хватит примерно на <b>{days_left:.1f}</b> дн.\n\n"
        "Все ваши ключи восстановлены, можно подключаться 🚀"
    )


async def check_notifications(bot: Bot) -> None:
    try:
        now = datetime.now(timezone.utc)
        soon = now + timedelta(hours=1)

        await _check_trial_expirations(bot, now, soon)
        await _check_low_balance(bot)

    except Exception as e:
        logger.error("check_notifications_failed err=%s", e, exc_info=True)


async def _check_trial_expirations(bot: Bot, now: datetime, soon: datetime) -> None:
    db = get_db()

    async with db.transaction() as conn:
        rows = await conn.fetch(
            """
            SELECT
                u.user_id,
                u.expire_at,
                COUNT(d.id) FILTER (WHERE d.is_active = TRUE) AS device_count,
                COALESCE(SUM(d.monthly_cost) FILTER (WHERE d.is_active = TRUE), 0) AS devices_monthly_cost
            FROM users u
            LEFT JOIN devices d ON d.user_id = u.user_id
            WHERE u.status = 'TRIAL'
              AND u.notified = FALSE
              AND u.expire_at IS NOT NULL
              AND u.expire_at > $1
              AND u.expire_at <= $2
            GROUP BY u.user_id, u.expire_at
            """,
            now,
            soon,
        )

        for row in rows:
            user_id = row["user_id"]
            device_count = row["device_count"] or 0
            devices_monthly_cents = row["devices_monthly_cost"] or 0

            try:
                await bot.send_message(
                    user_id,
                    _build_trial_message(device_count, devices_monthly_cents),
                    reply_markup=webapp_button(WEBAPP_URL, user_id),
                    parse_mode="HTML",
                )
                await conn.execute(
                    "UPDATE users SET notified = TRUE WHERE user_id = $1",
                    user_id,
                )
            except Exception as e:
                logger.error(
                    "notify_trial_expiration_failed user_id=%s err=%s",
                    user_id,
                    e,
                    exc_info=True,
                )

        await conn.execute(
            """
            UPDATE users
            SET notified = FALSE
            WHERE (
                status = 'TRIAL'
                AND notified = TRUE
                AND expire_at < $1
            )
            OR status <> 'TRIAL'
            """,
            now,
        )


async def _check_low_balance(bot: Bot) -> None:
    db = get_db()

    async with db.transaction() as conn:
        rows = await conn.fetch(
            """
            SELECT
                u.user_id,
                u.balance,
                u.low_balance_notified,
                COALESCE(u.low_balance_critical_notified, FALSE) AS low_balance_critical_notified,
                COUNT(d.id) AS total_device_count,
                COUNT(d.id) FILTER (WHERE d.is_active = TRUE) AS active_device_count,
                COALESCE(SUM(d.monthly_cost) FILTER (WHERE d.is_active = TRUE), 0) AS monthly_cost_cents
            FROM users u
            LEFT JOIN devices d ON d.user_id = u.user_id
            WHERE u.status = 'ACTIVE'
            GROUP BY
                u.user_id,
                u.balance,
                u.low_balance_notified,
                u.low_balance_critical_notified
            """
        )

        for row in rows:
            user_id = row["user_id"]
            balance_cents = row["balance"] or 0
            low_balance_notified = row["low_balance_notified"]
            low_balance_critical_notified = row["low_balance_critical_notified"]
            total_device_count = row["total_device_count"] or 0
            device_count = row["active_device_count"] or 0
            monthly_cost_cents = row["monthly_cost_cents"] or 0

            # Юзер без устройств — сбрасываем low-balance флаги.
            if total_device_count <= 0:
                if low_balance_notified or low_balance_critical_notified:
                    await conn.execute(
                        """
                        UPDATE users
                        SET low_balance_notified = FALSE,
                            low_balance_critical_notified = FALSE
                        WHERE user_id = $1
                        """,
                        user_id,
                    )
                continue

            # У пользователя могут быть только неактивные устройства после
            # временной блокировки. В таком случае low-balance уведомления
            # не шлём и не сбрасываем флаги, пока доступ не восстановится.
            if device_count <= 0 or monthly_cost_cents <= 0:
                continue

            # Hard suspension обрабатывается billing-слоем сразу в момент
            # деактивации. Здесь оставляем только предупреждения "до отключения".
            if balance_cents <= 0:
                continue

            days_left = _days_left(balance_cents, monthly_cost_cents)

            # Баланс восстановился выше раннего порога — сбрасываем оба warning-флага.
            if days_left > LOW_BALANCE_DAYS_THRESHOLD_EARLY:
                if low_balance_notified or low_balance_critical_notified:
                    await conn.execute(
                        """
                        UPDATE users
                        SET low_balance_notified = FALSE,
                            low_balance_critical_notified = FALSE
                        WHERE user_id = $1
                        """,
                        user_id,
                    )
                continue

            if days_left <= LOW_BALANCE_DAYS_THRESHOLD and low_balance_critical_notified:
                continue

            try:
                is_critical = days_left <= LOW_BALANCE_DAYS_THRESHOLD
                if not is_critical and low_balance_notified:
                    continue

                await bot.send_message(
                    user_id,
                    _build_low_balance_message(
                        balance_cents=balance_cents,
                        device_count=device_count,
                        monthly_cost_cents=monthly_cost_cents,
                        days_left=days_left,
                        critical=is_critical,
                    ),
                    reply_markup=webapp_button(WEBAPP_URL, user_id),
                    parse_mode="HTML",
                )

                if is_critical:
                    await conn.execute(
                        """
                        UPDATE users
                        SET low_balance_notified = TRUE,
                            low_balance_critical_notified = TRUE
                        WHERE user_id = $1
                        """,
                        user_id,
                    )
                    logger.info(
                        "notify_low_balance_critical.sent user_id=%s days_left=%s",
                        user_id, days_left,
                    )
                else:
                    await conn.execute(
                        "UPDATE users SET low_balance_notified = TRUE WHERE user_id = $1",
                        user_id,
                    )
                    logger.info(
                        "notify_low_balance_early.sent user_id=%s days_left=%s",
                        user_id, days_left,
                    )
            except Exception as e:
                logger.error(
                    "notify_low_balance_failed user_id=%s err=%s",
                    user_id,
                    e,
                    exc_info=True,
                )


async def check_reactivation_notifications(bot: Bot) -> None:
    """
    Отправляет уведомление юзерам у которых:
      - статус ACTIVE
      - баланс > 0
      - reactivation_notification_pending = TRUE
      - есть активные устройства
    Это значит — доступ ранее реально приостанавливался, а теперь восстановлен.
    Сбрасывает pending-флаг после отправки, либо тихо очищает его если баланс
    всё ещё слишком низкий для "всё снова хорошо" уведомления.
    """
    db = get_db()

    async with db.transaction() as conn:
        rows = await conn.fetch(
            """
            SELECT
                u.user_id,
                u.balance,
                COUNT(d.id) FILTER (WHERE d.is_active = TRUE) AS device_count,
                COALESCE(SUM(d.monthly_cost) FILTER (WHERE d.is_active = TRUE), 0) AS monthly_cost_cents
            FROM users u
            LEFT JOIN devices d ON d.user_id = u.user_id
            WHERE u.status = 'ACTIVE'
              AND u.balance > 0
              AND COALESCE(u.reactivation_notification_pending, FALSE) = TRUE
            GROUP BY u.user_id, u.balance
            HAVING COUNT(d.id) FILTER (WHERE d.is_active = TRUE) > 0
            """
        )

        for row in rows:
            user_id = row["user_id"]
            balance_cents = row["balance"]
            device_count = row["device_count"] or 0
            monthly_cost_cents = row["monthly_cost_cents"] or 0

            days_left = _days_left(balance_cents, monthly_cost_cents)

            # Если доступ восстановился, но денег всё ещё мало, не шлём
            # сообщение "всё снова ок" и не откладываем его на потом.
            if days_left <= LOW_BALANCE_DAYS_THRESHOLD_EARLY:
                await conn.execute(
                    """
                    UPDATE users
                    SET reactivation_notification_pending = FALSE
                    WHERE user_id = $1
                    """,
                    user_id,
                )
                logger.info(
                    "notify_reactivated.skip_low_balance user_id=%s days_left=%s",
                    user_id, days_left,
                )
                continue

            try:
                await bot.send_message(
                    user_id,
                    _build_reactivated_message(device_count, balance_cents, days_left),
                    parse_mode="HTML",
                )
                await conn.execute(
                    """
                    UPDATE users
                    SET reactivation_notification_pending = FALSE
                    WHERE user_id = $1
                    """,
                    user_id,
                )
                logger.info("notify_reactivated.sent user_id=%s", user_id)
            except Exception as e:
                logger.error(
                    "notify_reactivated_failed user_id=%s err=%s",
                    user_id,
                    e,
                    exc_info=True,
                )
