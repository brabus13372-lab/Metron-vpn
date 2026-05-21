import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import ADMIN_ID
from app.db import (
    get_all_users_with_devices,
    get_user_total_monthly_cost,
    charge_daily_billing_atomic,
    get_expired_trial_users,
    update_user_status,
    get_user_devices,
    deactivate_device,
)
from app.services.vpn import deactivate_all_user_devices

logger = logging.getLogger(__name__)


class BillingEngine:
    """
    Billing engine: charges users daily for their active devices.
    If balance reaches zero or is insufficient — deactivates all devices.
    Also handles trial expiry: deactivates devices and sets status to EXPIRED.
    """

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self._setup_jobs()

    def _setup_jobs(self):
        self.scheduler.add_job(
            self._daily_billing_cycle,
            trigger=CronTrigger(hour=0, minute=5),
            id="daily_billing",
            name="Daily device charge",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        logger.info("Billing engine: daily charge job scheduled at 00:05")

        self.scheduler.add_job(
            self._trial_expiry_cycle,
            trigger=CronTrigger(hour=0, minute=10),
            id="trial_expiry",
            name="Trial expiry deactivation",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        logger.info("Billing engine: trial expiry job scheduled at 00:10")

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _calculate_daily_cost_cents(self, monthly_cost_rub: Decimal) -> int:
        """
        Converts monthly cost (RUB) to a daily charge in kopecks.
        Rounding: ROUND_HALF_UP to nearest kopeck.
        """
        daily_rub = (monthly_cost_rub / Decimal("30")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        return int(daily_rub * 100)

    async def _notify_admin(self, text: str) -> None:
        if not ADMIN_ID:
            return
        try:
            from app.bot.dispatcher import dp  # noqa: PLC0415  (circular-import workaround)
            await dp.bot.send_message(ADMIN_ID, text, parse_mode="HTML")
        except Exception as exc:
            logger.error("billing.admin_notify_failed err=%s", exc, exc_info=True)

    async def _notify_user(self, user_id: int, text: str) -> None:
        try:
            from app.bot.dispatcher import dp  # noqa: PLC0415
            await dp.bot.send_message(user_id, text)
        except Exception as exc:
            logger.error(
                "billing.user_notify_failed user_id=%s err=%s", user_id, exc
            )

    # -----------------------------------------------------------------------
    # Daily billing
    # -----------------------------------------------------------------------

    async def _daily_billing_cycle(self):
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

    async def _process_user_billing(self, user_id: int):
        try:
            total_monthly_cost = await get_user_total_monthly_cost(user_id)
            if total_monthly_cost <= Decimal("0"):
                logger.debug("billing.skip user_id=%s reason=no_paid_devices", user_id)
                return

            billing_date = datetime.now().date()
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
                    "billing.insufficient_funds user_id=%s balance=%s daily_cost=%s monthly_cost=%s",
                    user_id, balance_before, daily_cost_rub, total_monthly_cost,
                )
                await self._deactivate_all_user_devices(user_id)
                await self._notify_admin(
                    f"<b>Недостаточно средств у пользователя {user_id}</b>\n"
                    f"Баланс: {balance_before:.2f}р\n"
                    f"Ежедневный расход: {daily_cost_rub:.2f}р\n"
                    f"Устройства деактивированы без списания."
                )
                return

            if not billing_result["charged"]:
                logger.warning("billing.skip user_id=%s reason=not_charged", user_id)
                return

            logger.info(
                "billing.charged user_id=%s balance_before=%s daily_cost=%s monthly_cost=%s balance_after=%s",
                user_id, balance_before, daily_cost_rub, total_monthly_cost, new_balance,
            )

            if new_balance == Decimal("0"):
                logger.warning(
                    "billing.balance_depleted user_id=%s balance_after=%s daily_cost=%s",
                    user_id, new_balance, daily_cost_rub,
                )
                await self._deactivate_all_user_devices(user_id)
                await self._notify_admin(
                    f"<b>Баланс пользователя {user_id} исчерпан</b>\n"
                    f"Баланс: {new_balance:.2f}р\n"
                    f"Ежедневный расход: {daily_cost_rub:.2f}р\n"
                    f"Все устройства деактивированы."
                )

        except Exception as exc:
            logger.error("billing.process_failed user_id=%s err=%s", user_id, exc, exc_info=True)

    # -----------------------------------------------------------------------
    # Trial expiry
    # -----------------------------------------------------------------------

    async def _trial_expiry_cycle(self):
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

            # deactivate_all_user_devices returns (success_count, fail_count, errors)
            success_count, fail_count, _errors = await deactivate_all_user_devices(user_id)

            devices = await get_user_devices(user_id)
            for dev in devices:
                await deactivate_device(dev["id"], user_id, reason="trial_expired")

            await update_user_status(user_id, "EXPIRED")

            logger.info(
                "trial_expiry.done user_id=%s devices_ok=%s devices_fail=%s",
                user_id, success_count, fail_count,
            )

            await self._notify_user(
                user_id,
                "Ваш бесплатный пробный период закончился.\n"
                "Доступ к VPN отключён.\n"
                "Чтобы продолжить пользоваться VPN, пополните баланс и подключите устройство.",
            )

            await self._notify_admin(
                f"<b>Триал истёк</b>\n"
                f"Пользователь: <code>{user_id}</code>\n"
                f"Устройств отключено: {success_count}\n"
                f"Ошибок: {fail_count}\n"
                f"Статус → EXPIRED"
            )

        except Exception as exc:
            logger.error("trial_expiry.failed user_id=%s err=%s", user_id, exc, exc_info=True)

    # -----------------------------------------------------------------------
    # Device deactivation (billing-triggered)
    # -----------------------------------------------------------------------

    async def _deactivate_all_user_devices(self, user_id: int):
        """
        Disables all user devices in 3X-UI panel and marks them inactive in DB.
        Notifies the user via bot.
        """
        logger.info("billing.deactivate_devices.start user_id=%s", user_id)

        # deactivate_all_user_devices returns (success_count, fail_count) — no errors list
        success_count, fail_count = await deactivate_all_user_devices(user_id)

        devices = await get_user_devices(user_id)
        for dev in devices:
            await deactivate_device(dev["id"], user_id, reason="insufficient_funds")

        logger.info(
            "billing.deactivate_devices.done user_id=%s ok=%s fail=%s",
            user_id, success_count, fail_count,
        )

        if fail_count == 0:
            user_text = (
                "Ваш баланс исчерпан или недостаточен для продления устройств.\n"
                "Все VPN-подключения временно отключены.\n"
                "Пополните баланс в профиле, чтобы восстановить доступ."
            )
        else:
            user_text = (
                "Ваш баланс исчерпан или недостаточен для продления устройств.\n"
                "Мы попытались отключить VPN-подключения, но часть операций могла завершиться с ошибкой.\n"
                "Пополните баланс в профиле и при необходимости обратитесь в поддержку."
            )

        await self._notify_user(user_id, user_text)

        if fail_count > 0:
            await self._notify_admin(
                f"<b>Частичная ошибка деактивации устройств</b>\n"
                f"Пользователь: <code>{user_id}</code>\n"
                f"Успешно: {success_count}\n"
                f"Ошибок: {fail_count}"
            )

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Billing engine started")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Billing engine stopped")


# Global billing engine instance
billing_engine = BillingEngine()
