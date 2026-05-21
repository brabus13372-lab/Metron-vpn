import html
import logging
from datetime import datetime

from aiogram import Bot, F, types
from aiogram.types import LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.dispatcher import dp
from app.config import ADMIN_ID, PAY_TOKEN, PAYMENT_AMOUNTS
from app.db import (
    activate_device,
    add_balance_atomic,
    ensure_user_stub,
    get_user_data_dict,
    get_user_devices,
    record_payment_idempotent,
    save_paid_access,
    update_user_status,
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
    """
    Проверяем payload и что сумма входит в список допустимых.
    Формат payload: vpn_pay_{user_id}_{timestamp}_{amount_cents}
    """
    payload = payment.invoice_payload

    if not payload.startswith("vpn_pay_"):
        return False

    parts = payload.split("_", 4)
    if len(parts) < 5:
        return False

    try:
        if int(parts[2]) != user_id:
            return False
        expected_amount = int(parts[4])
    except ValueError:
        return False

    # Сумма в payload должна совпадать с реально уплаченной
    if payment.total_amount != expected_amount:
        return False

    # Сумма должна быть из списка допустимых
    try:
        from app.config import PAYMENT_AMOUNTS
        if payment.total_amount not in PAYMENT_AMOUNTS:
            return False
    except ImportError:
        pass  # Если PAYMENT_AMOUNTS не задан — не валидируем список

    return True


async def _execute_activation(user_id: int, bot: Bot) -> None:
    """Активирует устройства в панели и синхронизирует is_active в БД."""
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
        return

    try:
        devices = await get_user_devices(user_id)
        for dev in devices:
            await activate_device(dev["id"], user_id)
    except Exception as e:
        logger.exception("Failed to sync is_active after activation user=%s", user_id)
        await _safe_alert_admin(
            bot,
            f"🚨 DB SYNC FAIL after activation user={user_id}: {html.escape(str(e))}",
        )


@dp.callback_query(F.data == "buy_vpn")
async def show_payment_options(call: types.CallbackQuery) -> None:
    """Показываем пресеты суммы для пополнения."""
    await call.answer()

    try:
        from app.config import PAYMENT_AMOUNTS
        amounts = PAYMENT_AMOUNTS
    except ImportError:
        amounts = [10000, 20000, 50000, 100000]

    builder = InlineKeyboardBuilder()
    for amount_cents in amounts:
        rub = amount_cents // 100
        builder.row(
            types.InlineKeyboardButton(
                text=f"💳 Пополнить на {rub} руб.",
                callback_data=f"pay_amount_{amount_cents}",
            )
        )
    builder.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_profile"))

    await call.message.edit_text(
        "<b>💳 Пополнение баланса</b>\n\nВыберите сумму:",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@dp.callback_query(F.data.startswith("pay_amount_"))
async def send_invoice(call: types.CallbackQuery, bot: Bot) -> None:
    """Выставляем счёт на выбранную сумму."""
    try:
        amount_cents = int(call.data.split("_", 2)[2])
    except (IndexError, ValueError):
        await call.answer("❌ Ошибка", show_alert=True)
        return

    try:
        from app.config import PAYMENT_AMOUNTS
        if amount_cents not in PAYMENT_AMOUNTS:
            await call.answer("❌ Недопустимая сумма", show_alert=True)
            return
    except ImportError:
        pass

    await call.answer()

    payload = f"vpn_pay_{call.from_user.id}_{int(datetime.now().timestamp())}_{amount_cents}"
    rub = amount_cents // 100

    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="MetronVPN — пополнение баланса",
        description=f"Пополнение баланса на {rub} руб.",
        payload=payload,
        provider_token=PAY_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label=f"Баланс +{rub} руб.", amount=amount_cents)],
    )


@dp.pre_checkout_query(F.invoice_payload.startswith("vpn_pay_"))
async def pre_checkout(q: types.PreCheckoutQuery, bot: Bot) -> None:
    """
    Проверяем payload: извлекаем amount из него и сверяем с q.total_amount.
    """
    try:
        parts = q.invoice_payload.split("_", 4)
        expected_amount = int(parts[4])
    except (IndexError, ValueError):
        await bot.answer_pre_checkout_query(
            q.id, ok=False, error_message="Неверный формат платежа"
        )
        return

    if q.total_amount != expected_amount:
        await bot.answer_pre_checkout_query(
            q.id, ok=False, error_message="Неверная сумма платежа"
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

    # 1. Гарантируем, что пользователь есть в БД
    try:
        await ensure_user_stub(
            user_id=user_id,
            username=(message.from_user.username or f"user_{user_id}")[:32],
        )
    except Exception as e:
        logger.exception("Failed to ensure user stub user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 USER STUB FAIL {html.escape(str(e))}")
        return

    # 2. Идемпотентная запись платежа
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

    # 3. Пополнение баланса
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

    # 4. Получаем данные пользователя
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
    current_status = user_data.get("status", "NEW")

    # 5. Создаём основной ключ если его ещё нет
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
        elif current_status not in ("ACTIVE", "TRIAL"):
            # Ключ есть, но статус не активный — просто меняем статус
            await update_user_status(user_id, "ACTIVE")

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

    # 6. Активируем устройства в панели
    await _execute_activation(user_id, bot)

    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="📖 Инструкция", callback_data="show_instruction")
    )
    builder.row(
        types.InlineKeyboardButton(text="📱 Мои устройства", callback_data="manage_devices")
    )

    await message.answer(
        "🎉 <b>Оплата прошла успешно!</b>\n"
        f"<b>💰 Баланс:</b> {new_balance:.2f} ₽\n"
        "Доступ активирован.",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
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
        f"💰 Payment OK user={user_id} amount={amount_cents // 100}р balance={new_balance:.2f}р",
    )