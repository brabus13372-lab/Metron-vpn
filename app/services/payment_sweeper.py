import logging

from aiogram import Bot

from app.config import ADMIN_ID
from app.db import apply_payment_topup_idempotent, list_received_payment_ids

logger = logging.getLogger(__name__)


async def sweep_received_payments(bot: Bot | None = None) -> tuple[int, int]:
    """
    Retry apply for payments stuck in RECEIVED.
    Idempotent — safe to run on a schedule.
    """
    payment_ids = await list_received_payment_ids()
    if not payment_ids:
        return 0, 0

    applied = 0
    failed = 0
    for payment_id in payment_ids:
        try:
            result = await apply_payment_topup_idempotent(payment_id)
            applied += 1
            logger.info(
                "payment_sweeper.applied payment_id=%s already_applied=%s balance=%s",
                payment_id,
                result.get("already_applied"),
                result.get("balance"),
            )
        except Exception:
            failed += 1
            logger.exception("payment_sweeper.failed payment_id=%s", payment_id)

    if failed and bot and ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                "⚠️ <b>Payment sweeper</b>\n"
                f"Applied: {applied}, failed: {failed}, scanned: {len(payment_ids)}",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("payment_sweeper.admin_notify_failed")

    return applied, failed
