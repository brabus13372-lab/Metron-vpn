import html
import logging
from datetime import datetime

from aiogram import Bot, F, types
from aiogram.types import LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.dispatcher import dp
from app.config import ADMIN_ID, PAY_TOKEN, PAYMENT_AMOUNT
from app.db import (
    ensure_user_stub,
    get_user_data_dict,
    record_payment_idempotent,
    add_balance_atomic,
    save_paid_access,
)
from app.services.vpn import (
    activate_all_user_devices,
    create_panel_client,
)

logger = logging.getLogger(__name__)


async def _safe_alert_admin(bot: Bot, text: str) -> None:
    if not ADMIN_ID:
        return
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
    except Exception:
        logger.exception("ADMIN ALERT FAILED")


def _validate_payment(payment: types.SuccessfulPayment, user_id: int) -> bool:
    payload = payment.invoice_payload

    if not payload.startswith("vpn_pay_"):
        return False

    parts = payload.split("_", 3)
    if len(parts) < 4:
        return False

    try:
        if int(parts[2]) != user_id:
            return False
    except ValueError:
        return False

    if payment.total_amount != PAYMENT_AMOUNT:
        return False

    return True


async def _execute_activation(user_id: int, bot: Bot) -> None:
    try:
        success, failed, errors = await activate_all_user_devices(user_id)

        if success == 0 and failed > 0:
            await _safe_alert_admin(
                bot,
                f"🚨 Activation failed user={user_id} errors={html.escape(str(errors[:3]))}",
            )
    except Exception as e:
        await _safe_alert_admin(
            bot,
            f"🚨 CRITICAL activation error user={user_id}: {html.escape(str(e))}",
        )


@dp.callback_query(F.data == "buy_vpn")
async def send_invoice(call: types.CallbackQuery, bot: Bot) -> None:
    await call.answer()

    payload = f"vpn_pay_{call.from_user.id}_{int(datetime.now().timestamp())}"

    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="MetronVPN",
        description="Пополнение баланса VPN",
        payload=payload,
        provider_token=PAY_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="VPN balance top-up", amount=PAYMENT_AMOUNT)],
    )


@dp.pre_checkout_query(F.invoice_payload.startswith("vpn_pay_"))
async def pre_checkout(q: types.PreCheckoutQuery, bot: Bot) -> None:
    if q.total_amount != PAYMENT_AMOUNT:
        await bot.answer_pre_checkout_query(
            q.id,
            ok=False,
            error_message="Неверная сумма платежа",
        )
        return

    await bot.answer_pre_checkout_query(q.id, ok=True)


@dp.message(F.successful_payment)
async def success_payment(message: types.Message, bot: Bot) -> None:
    user_id = message.from_user.id
    payment = message.successful_payment

    if payment is None:
        logger.error("successful_payment handler called without payment object user=%s", user_id)
        return

    amount_cents = payment.total_amount

    if not _validate_payment(payment, user_id):
        logger.warning(
            "Invalid payment received user=%s payload=%s",
            user_id,
            getattr(payment, "invoice_payload", None),
        )
        await _safe_alert_admin(bot, f"🚨 INVALID PAYMENT user={user_id}")
        return

    try:
        await ensure_user_stub(
            user_id=user_id,
            username=(message.from_user.username or f"user_{user_id}")[:32],
        )
    except Exception as e:
        logger.exception("Failed to ensure user stub user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 USER STUB FAIL {html.escape(str(e))}")
        return

    try:
        payment_result = await record_payment_idempotent(
            user_id=user_id,
            payload=payment.invoice_payload,
            telegram_charge_id=payment.telegram_payment_charge_id,
            provider_charge_id=payment.provider_payment_charge_id,
            amount_cents=amount_cents,
        )

        if not payment_result["created"]:
            logger.warning(
                "Duplicate payment ignored user=%s provider_charge_id=%s",
                user_id,
                payment.provider_payment_charge_id,
            )
            return

    except Exception as e:
        logger.exception("Failed to save payment user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 PAYMENT SAVE FAIL {html.escape(str(e))}")
        return

    try:
        new_balance = await add_balance_atomic(
            user_id=user_id,
            amount_cents=amount_cents,
            reference_type="payment",
            reference_id=payment.provider_payment_charge_id,
            idempotency_key=f"payment:{payment.provider_payment_charge_id}",
        )
        if new_balance is None:
            logger.error("User not found during balance top-up user=%s", user_id)
            await _safe_alert_admin(bot, f"🚨 USER NOT FOUND {user_id}")
            return

    except Exception as e:
        logger.exception("Balance update failed user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 DB ERROR {html.escape(str(e))}")
        return

    try:
        user_data = await get_user_data_dict(user_id)
    except Exception as e:
        logger.exception("Failed to fetch user data user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 USER DATA FAIL {html.escape(str(e))}")
        return

    if not user_data:
        logger.error("User data missing after successful balance update user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 USER DATA MISSING {user_id}")
        return

    username = (user_data.get("username") or f"user_{user_id}")[:32]
    vless_link = user_data.get("vless_link")
    uuid = user_data.get("uuid")

    try:
        if not vless_link or not uuid:
            vless_link, uuid, err = await create_panel_client(
                user_id=user_id,
                username=username,
            )
            if not vless_link or not uuid:
                raise RuntimeError(err or "create_panel_client returned empty result")

        await save_paid_access(
            user_id=user_id,
            username=username,
            vless_link=vless_link,
            uuid_val=uuid,
        )

    except Exception as e:
        logger.exception("VPN/user sync failed user=%s", user_id)
        await _safe_alert_admin(
            bot,
            f"🚨 VPN/SAVE FAIL user={user_id} err={html.escape(str(e))}",
        )
        await message.answer(
            "⚠️ Оплата прошла, баланс пополнен, но при выдаче доступа произошла ошибка. Мы уже разберёмся.",
            disable_web_page_preview=True,
        )
        return

    await _execute_activation(user_id, bot)

    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(
            text="📖 Инструкция",
            callback_data="show_instruction",
        )
    )
    kb = builder.as_markup()

    await message.answer(
        "🎉 Оплата прошла успешно\n"
        f"Баланс: {new_balance:.2f} ₽\n"
        "Доступ активирован.",
        reply_markup=kb,
        disable_web_page_preview=True,
    )

    logger.info(
        "PAYMENT SUCCESS user=%s amount_cents=%s balance=%s",
        user_id,
        amount_cents,
        new_balance,
    )

    await _safe_alert_admin(
        bot,
        f"💰 Payment OK user={user_id} amount={amount_cents}",
    )