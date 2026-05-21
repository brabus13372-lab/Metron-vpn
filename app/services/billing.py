import logging
from datetime import datetime

from app.config import ADMIN_ID

from decimal import Decimal, ROUND_HALF_UP

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db import (
    get_all_users_with_devices,
    get_user_total_monthly_cost,
    charge_daily_billing_atomic,
)

from app.services.vpn import deactivate_all_user_devices

logger = logging.getLogger(__name__)


class BillingEngine:
    """Биллинг-движок: ежедневно списывает с баланса пользователей
    стоимость их устройств. Если баланс <= 0 — деактивирует устройства."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self._setup_jobs()

    def _setup_jobs(self):
        """Настраивает задачи планировщика"""
        # Запуск каждый день в 00:05
        self.scheduler.add_job(
            self._daily_billing_cycle,
            trigger=CronTrigger(hour=0, minute=5),
            id="daily_billing",
            name="Ежедневное списание за устройства",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        logger.info(" Биллинг-движок: задача ежедневного списания настроена (00:05)")

    def _calculate_daily_cost_cents(self, monthly_cost_rub: Decimal) -> int:
        """
        Переводит месячную стоимость в ежедневное списание в копейках.
        Округление: до ближайшей копейки.
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
            from app.bot.dispatcher import dp
            bot = dp.bot
            await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
        except Exception as notify_err:
            logger.error("billing.admin_notify_failed err=%s", notify_err, exc_info=True)

    async def _daily_billing_cycle(self):
        """Основной цикл ежедневного списания"""
        logger.info(" Начало ежедневного биллинг-цикла...")
        try:
            user_ids = await get_all_users_with_devices()
            if not user_ids:
                logger.info(" Нет пользователей с устройствами для списания")
                return

            logger.info(f" Найдено пользователей с устройствами: {len(user_ids)}")

            for user_id in user_ids:
                await self._process_user_billing(user_id)

            logger.info(" Биллинг-цикл завершён")
        except Exception as e:
            logger.error(f" Критическая ошибка в биллинг-цикле: {e}", exc_info=True)

    async def _process_user_billing(self, user_id: int):
        """Обрабатывает списание для одного пользователя"""
        try:
            total_monthly_cost = await get_user_total_monthly_cost(user_id)
            if total_monthly_cost <= Decimal("0"):
                logger.debug("user_id=%s skip billing reason=no_paid_devices", user_id)
                return

            now = datetime.now()
            billing_date = now.date()

            daily_cost_cents = self._calculate_daily_cost_cents(total_monthly_cost)
            daily_cost_rub = Decimal(daily_cost_cents) / 100

            billing_result = await charge_daily_billing_atomic(
                user_id,
                daily_cost_cents,
                billing_date,
            )

            if billing_result is None:
                logger.warning("user_id=%s skip billing reason=user_not_found", user_id)
                return

            if billing_result["already_charged_today"]:
                logger.info("user_id=%s skip billing reason=already_charged_today", user_id)
                return

            balance_before = billing_result["balance_before"]
            new_balance = billing_result["balance_after"]

            if billing_result["insufficient_funds"]:
                logger.warning(
                    "user_id=%s billing skipped reason=insufficient_funds balance=%s daily_cost=%s monthly_cost=%s",
                    user_id,
                    balance_before,
                    daily_cost_rub,
                    total_monthly_cost,
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
                logger.warning("user_id=%s billing skipped reason=not_charged", user_id)
                return

            logger.info(
                "user_id=%s billed balance_before=%s daily_cost=%s monthly_cost=%s balance_after=%s",
                user_id,
                balance_before,
                daily_cost_rub,
                total_monthly_cost,
                new_balance,
            )

            if new_balance == Decimal("0"):
                logger.warning(
                    "user_id=%s balance_depleted balance_after=%s daily_cost=%s",
                    user_id,
                    new_balance,
                    daily_cost_rub,
                )
                await self._deactivate_all_user_devices(user_id)

                await self._notify_admin(
                    f"<b>Баланс пользователя {user_id} исчерпан</b>\n"
                    f"Баланс: {new_balance:.2f}р\n"
                    f"Ежедневный расход: {daily_cost_rub:.2f}р\n"
                    f"Все устройства деактивированы."
                )

        except Exception as e:
            logger.error("billing_process_failed user_id=%s err=%s", user_id, e, exc_info=True)

    async def _deactivate_all_user_devices(self, user_id: int):
        """Деактивирует все устройства пользователя в панели 3X-UI и уведомляет юзера"""
        logger.info("billing.deactivate_devices.start user_id=%s", user_id)

        success_count, fail_count = await deactivate_all_user_devices(user_id)

        logger.info(
            "billing.deactivate_devices.done user_id=%s success_count=%s fail_count=%s",
            user_id,
            success_count,
            fail_count,
        )

        try:
            from app.bot.dispatcher import dp
            bot = dp.bot

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

            await bot.send_message(user_id, user_text)

            logger.info(
                "billing.deactivate_devices.user_notified user_id=%s fail_count=%s",
                user_id,
                fail_count,
            )

        except Exception as notify_err:
            logger.error(
                "billing.deactivate_devices.user_notify_failed user_id=%s err=%s",
                user_id,
                notify_err,
            )

        if fail_count > 0:
            await self._notify_admin(
                f"<b>Частичная ошибка деактивации устройств</b>\n"
                f"Пользователь: <code>{user_id}</code>\n"
                f"Успешно: {success_count}\n"
                f"Ошибок: {fail_count}"
            )
            
    def start(self):
        """Запускает планировщик биллинга"""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info(" Биллинг-движок запущен")

    def stop(self):
        """Останавливает планировщик биллинга"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info(" Биллинг-движок остановлен")


# Глобальный экземпляр биллинг-движка
billing_engine = BillingEngine()
