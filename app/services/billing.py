import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import ADMIN_ID
from app.db import (
    charge_daily_billing_atomic,
    get_all_users_with_devices,
    get_expired_trial_users,
    get_user_total_monthly_cost,
    set_reactivation_notification_pending,
    update_user_status,
)
from app.services.vpn import deactivate_all_user_devices

logger = logging.getLogger(__name__)


class BillingEngine:
    """
    Billing engine: charges users daily for their active devices.
    If balance reaches zero or is insufficient — deactivates all devices.
    Also handles trial expiry.
    """

    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self._bot: Bot | None = None
        self._setup_jobs()

    def set_bot(self, bot: Bot) -> None:
        self._bot = bot

    def _setup_jobs(self) -> None:
        # Ежедневное списание — 00:05 MSK
        self.scheduler.add_job(
            self._daily_billing_cycle,
            trigger=CronTrigger(hour=0, minute=5, timezone="Europe/Moscow"),
            id="daily_billing",
            name="Daily device charge",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        # Проверка истёкших триалов — каждый час (догоняет пропущенные после рестарта)
        self.scheduler.add_job(
            self._trial_expiry_cycle,
            trigger=IntervalTrigger(hours=1),
            id="trial_expiry",
            name="Trial expiry deactivation (hourly)",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        logger.info("Billing jobs scheduled: billing=00:05 MSK, trial=every 1h")

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _calculate_daily_cost_cents(self, monthly_cost_rub: Decimal) -> int:
        daily_rub = (monthly_cost_rub / Decimal("30")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return int(daily_rub * 100)

    async def _notify_admin(self, text: str) -> None:
        if not ADMIN_ID or not self._bot:
            return
        try:
            await self._bot.send_message(ADMIN_ID, text, parse_mode="HTML")
        except Exception as exc:
            logger.error("billing.admin_notify_failed err=%s", exc)

    async def _notify_user(self, user_id: int, text: str) -> None:
        if not self._bot:
            return
        try:
            await self._bot.send_message(user_id, text)
        except Exception as exc:
            logger.error("billing.user_notify_failed user_id=%s err=%s", user_id, exc)

    # -----------------------------------------------------------------------
    # Daily billing
    # -----------------------------------------------------------------------

    async def _daily_billing_cycle(self) -> None:
        logger.info("billing.daily_cycle.start")
        try:
            user_ids = await get_all_users_with_devices()
            if not user_ids:
                logger.info("billing.daily_cycle.skip reason=no_users_with_devices")
                return

            logger.info("billing.daily_cycle.users_found count=%s", len(user_ids))
            for user_id in user_ids:
                await self._process_user_billing(user_id)
            logger.info("billing.daily_cycle.done")
        except Exception as exc:
            logger.error("billing.daily_cycle.critical_error err=%s", exc, exc_info=True)

    async def _process_user_billing(self, user_id: int) -> None:
        try:
            total_monthly_cost = await get_user_total_monthly_cost(user_id)
            if total_monthly_cost <= Decimal("0"):
                logger.debug("billing.skip user_id=%s reason=no_paid_devices", user_id)
                return

            from zoneinfo import ZoneInfo
            billing_date = datetime.now(ZoneInfo("Europe/Moscow")).date()

            daily_cost_cents = self._calculate_daily_cost_cents(total_monthly_cost)
            daily_cost_rub = Decimal(daily_cost_cents) / 100

            billing_result = await charge_daily_billing_atomic(
                user_id, daily_cost_cents, billing_date
            )

            if billing_result is None:
                logger.warning("billing.skip user_id=%s reason=user_not_found", user_id)
                return

            if billing_result["already_charged_today"]:
                logger.info("billing.skip user_id=%s reason=already_charged_today", user_id)
                return

            balance_before = billing_result["balance_before"]
            new_balance = billing_result["balance_after"]

            if billing_result["insufficient_funds"]:
                logger.warning(
                    "billing.insufficient_funds user_id=%s balance=%s daily=%s",
                    user_id, balance_before, daily_cost_rub,
                )
                await self._deactivate_all_user_devices(user_id)
                await self._notify_admin(
                    f"<b>Недостаточно средств у пользователя {user_id}</b>\n"
                    f"Баланс: {balance_before:.2f}р\nЕжедневный расход: {daily_cost_rub:.2f}р\n"
                    "Устройства деактивированы без списания."
                )
                return

            if not billing_result["charged"]:
                logger.warning("billing.skip user_id=%s reason=not_charged", user_id)
                return

            logger.info(
                "billing.charged user_id=%s before=%s daily=%s after=%s",
                user_id, balance_before, daily_cost_rub, new_balance,
            )

            if new_balance <= Decimal("0"):
                await self._deactivate_all_user_devices(user_id)
                await self._notify_admin(
                    f"<b>Баланс пользователя {user_id} исчерпан</b>\n"
                    f"Баланс: {new_balance:.2f}р\nЕжедневный расход: {daily_cost_rub:.2f}р\n"
                    "Все устройства деактивированы."
                )

        except Exception as exc:
            logger.error("billing.process_failed user_id=%s err=%s", user_id, exc, exc_info=True)

    # -----------------------------------------------------------------------
    # Trial expiry (hourly)
    # -----------------------------------------------------------------------

    async def _trial_expiry_cycle(self) -> None:
        logger.info("trial_expiry.cycle.start")
        try:
            user_ids = await get_expired_trial_users()
            if not user_ids:
                logger.info("trial_expiry.cycle.skip reason=no_expired_trials")
                return

            logger.info("trial_expiry.cycle.users_found count=%s", len(user_ids))
            for user_id in user_ids:
                await self._process_trial_expiry(user_id)
            logger.info("trial_expiry.cycle.done")
        except Exception as exc:
            logger.error("trial_expiry.cycle.critical_error err=%s", exc, exc_info=True)

    async def _process_trial_expiry(self, user_id: int):
        try:
            logger.info("trial_expiry.start user_id=%s", user_id)
            # Триал истёк — переводим в ACTIVE (устройства продолжают работать,
            # теперь будет идти ежедневное списание)
            await update_user_status(user_id, "ACTIVE")
            logger.info("trial_expiry.done user_id=%s", user_id)

            await self._notify_user(
                user_id,
                "Ваш бесплатный пробный период закончился.\n"
                "Теперь доступ к VPN тарифицируется ежедневно.\n"
                "Пополните баланс в профиле, чтобы не потерять доступ."
            )
            await self._notify_admin(
                f"<b>Триал истёк</b>\n"
                f"Пользователь: <code>{user_id}</code>\n"
                f"Статус → ACTIVE, устройства продолжают работать."
            )
        except Exception as exc:
            logger.error("trial_expiry.failed user_id=%s err=%s", user_id, exc, exc_info=True)

    # -----------------------------------------------------------------------
    # Device deactivation (billing-triggered)
    # -----------------------------------------------------------------------

    async def _deactivate_all_user_devices(self, user_id: int) -> None:
        logger.info("billing.deactivate_devices.start user_id=%s", user_id)
        success_count, fail_count = await deactivate_all_user_devices(
            user_id,
            reason="insufficient_funds",
        )

        # Меняем статус юзера: EXPIRED — деньги кончились, доступ отозван.
        # payments.py вернёт статус в ACTIVE при следующем пополнении.
        await update_user_status(user_id, "EXPIRED")

        logger.info(
            "billing.deactivate_devices.done user_id=%s ok=%s fail=%s status=EXPIRED",
            user_id, success_count, fail_count,
        )

        user_text = (
            "Ваш баланс исчерпан или недостаточен для продления устройств.\n"
            "Все VPN-подключения временно отключены.\n"
            "Пополните баланс в профиле, чтобы восстановить доступ."
            if fail_count == 0 else
            "Ваш баланс исчерпан или недостаточен для продления устройств.\n"
            "Мы попытались отключить VPN-подключения, но часть операций могла завершиться с ошибкой.\n"
            "Пополните баланс и при необходимости обратитесь в поддержку."
        )
        await self._notify_user(user_id, user_text)
        if success_count > 0:
            await set_reactivation_notification_pending(user_id, True)

        if fail_count > 0:
            await self._notify_admin(
                f"<b>Частичная ошибка деактивации устройств</b>\n"
                f"Пользователь: <code>{user_id}</code>\n"
                f"Успешно: {success_count}\nОшибок: {fail_count}"
            )

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Billing engine started")

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Billing engine stopped")


# Синглтон — импортируется как `from app.services.billing import billing_engine`
billing_engine = BillingEngine()
