import asyncio
import html
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, types
from aiogram.types import LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.dispatcher import dp
from app.config import ADMIN_ID, PAY_TOKEN, PAYMENT_AMOUNT

from app.db import (
    add_balance_atomic,
    get_user_data_dict,
    save_user,
    get_db_connection,
)

from app.services.vpn import (
    activate_all_user_devices,
    create_panel_client,
    update_panel_client,
)

logger = logging.getLogger(__name__)

SUBSCRIPTION_DAYS = 30


# =========================
# DB PAYMENT
# =========================

def save_payment_if_new(user_id: int, payment) -> bool:
    """
    True  -> новый платеж
    False -> дубль
    """
    with get_db_connection(commit=True) as conn:
        try:
            conn.execute("""
                INSERT INTO payments (
                    user_id,
                    payload,
                    telegram_charge_id,
                    provider_charge_id,
                    amount
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                user_id,
                payment.invoice_payload,
                payment.telegram_payment_charge_id,
                payment.provider_payment_charge_id,
                payment.total_amount
            ))
            logger.debug("New payment saved user=%s", user_id)
            return True

        except sqlite3.IntegrityError:
            return False


# =========================
# HELPERS
# =========================

async def _safe_alert_admin(bot: Bot, text: str):
    if not ADMIN_ID:
        return
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
    except Exception:
        logger.exception("ADMIN ALERT FAILED")


def _validate_payment(payment, user_id: int) -> bool:
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


def _parse_expire(expire_raw: str | None) -> datetime:
    if not expire_raw:
        return datetime.now(timezone.utc)

    try:
        clean = expire_raw.replace(" ", "T")
        dt = datetime.fromisoformat(clean)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        try:
            return datetime.strptime(
                expire_raw, "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)


async def _execute_activation(user_id: int, bot: Bot):
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


# =========================
# HANDLERS
# =========================

@dp.callback_query(F.data == "buy_vpn")
async def send_invoice(call: types.CallbackQuery, bot: Bot):
    await call.answer()

    payload = f"vpn_pay_{call.from_user.id}_{int(datetime.now().timestamp())}"

    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="MetronVPN",
        description=f"{SUBSCRIPTION_DAYS} дней доступа",
        payload=payload,
        provider_token=PAY_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="VPN", amount=PAYMENT_AMOUNT)],
    )


@dp.pre_checkout_query(F.invoice_payload.startswith("vpn_pay_"))
async def pre_checkout(q: types.PreCheckoutQuery, bot: Bot):
    if q.total_amount != PAYMENT_AMOUNT:
        await bot.answer_pre_checkout_query(
            q.id,
            ok=False,
            error_message="Неверная сумма платежа"
        )
        return

    await bot.answer_pre_checkout_query(q.id, ok=True)


@dp.message(F.successful_payment)
async def success_payment(message: types.Message, bot: Bot):
    user_id = message.from_user.id
    payment = message.successful_payment

    if payment is None:
        logger.error("successful_payment handler called without payment object user=%s", user_id)
        return

    amount = payment.total_amount

    #=========================
    # VALIDATION
    # =========================
    if not _validate_payment(payment, user_id):
        logger.warning("Invalid payment received user=%s payload=%s", user_id, getattr(payment, "invoice_payload", None))
        await _safe_alert_admin(bot, f"🚨 INVALID PAYMENT user={user_id}")
        return

    # =========================
    # IDEMPOTENCY
    # =========================
    try:
        is_new = await asyncio.to_thread(save_payment_if_new, user_id, payment)
        if not is_new:
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

    # =========================
    # BALANCE
    # =========================
    try:
        new_balance = await asyncio.to_thread(add_balance_atomic, user_id, amount)
        if new_balance is None:
            logger.error("User not found during balance top-up user=%s", user_id)
            await _safe_alert_admin(bot, f"🚨 USER NOT FOUND {user_id}")
            return

    except Exception as e:
        logger.exception("Balance update failed user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 DB ERROR {html.escape(str(e))}")
        return

    # =========================
    # USER DATA
    # =========================
    try:
        user_data = await asyncio.to_thread(get_user_data_dict, user_id)
    except Exception as e:
        logger.exception("Failed to fetch user data user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 USER DATA FAIL {html.escape(str(e))}")
        return

    if not user_data:
        logger.error("User data missing after successful balance update user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 USER DATA MISSING {user_id}")
        return

    now = datetime.now(timezone.utc)
    expire_raw = user_data.get("expire_at")
    current_expire = _parse_expire(expire_raw)
    start = max(now, current_expire)
    new_expire = start + timedelta(days=SUBSCRIPTION_DAYS)

    expire_str = new_expire.strftime("%Y-%m-%d %H:%M:%S")
    target_ts = int(new_expire.timestamp() * 1000)

    username = (user_data.get("username") or f"user_{user_id}")[:32]
    vless_link = user_data.get("vless_link")
    uuid = user_data.get("uuid")

    # =========================
    # VPN PANEL
    # =========================
    try:
        if not vless_link or not uuid:
            vless_link, uuid, err = await create_panel_client(
                user_id,
                username,
                days=SUBSCRIPTION_DAYS,
            )
            if not vless_link or not uuid:
                raise RuntimeError(err or "create_panel_client returned empty result")
        else:
            ok, err = await update_panel_client(
                uuid,
                user_id,
                username,
                target_ts,
            )
            if not ok:
                raise RuntimeError(err or "update_panel_client failed")
    except Exception as e:
        logger.exception("VPN panel operation failed user=%s", user_id)
        await _safe_alert_admin(
            bot,
            f"🚨 VPN FAIL user={user_id} err={html.escape(str(e))}",
        )
        await message.answer(
            "⚠️ Оплата прошла, но произошла проблема, доступ будет выдан, прошу подождите",
            disable_web_page_preview=True,
        )
        return

    #=========================
    # SAVE USER
    # =========================
    try:
        await asyncio.to_thread(
            save_user,
            user_id,
            username,
            expire_str,
            vless_link,
            uuid,
            "PAID",
        )
    except Exception as e:
        logger.exception("Failed to save user after payment user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 SAVE USER FAIL {html.escape(str(e))}")
        return

    # =========================
    # RESPONSE
    # =========================
    kb = InlineKeyboardBuilder().row(
        types.InlineKeyboardButton(
            text="📖 Инструкция",
            callback_data="show_instruction",
        )
    ).as_markup()

    await message.answer(
        f"🎉 Оплата прошла\n"
        f"Доступ до: {expire_str}\n"
        f"Баланс: {new_balance:.2f}",
        reply_markup=kb,
        disable_web_page_preview=True,
    )

    logger.info(
        "PAYMENT SUCCESS user=%s amount=%s balance=%s expire_at=%s",
        user_id,
        amount,
        new_balance,
        expire_str,
    )

    await _safe_alert_admin(
        bot,
        f"💰 Payment OK user={user_id} amount={amount}",
    )