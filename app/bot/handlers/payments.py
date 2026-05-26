import html
import logging
from datetime import datetime

from aiogram import Bot, F, types
from aiogram.types import LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.dispatcher import dp
from app.bot.keyboards import webapp_button
from app.config import ADMIN_ID, PAY_TOKEN, PAYMENT_AMOUNTS, WEBAPP_URL
from app.db import (
    apply_payment_topup_idempotent,
    ensure_user_stub,
    get_user_data_dict,
    get_user_devices,
    record_payment_idempotent,
    set_reactivation_notification_pending,
    update_user_status,
)
from app.services.vpn import activate_all_user_devices

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

    if payment.total_amount != expected_amount:
        return False

    try:
        from app.config import PAYMENT_AMOUNTS
        if payment.total_amount not in PAYMENT_AMOUNTS:
            return False
    except ImportError:
        pass

    return True


async def _execute_activation(user_id: int, bot: Bot) -> tuple[int, int, list]:
    """
    Активирует устройства в панели.

    Устройства с disabled_reason='user_request' пропускаются —
    выбор пользователя сохраняется после пополнения баланса.
    Синхронизация is_active выполняется внутри service-слоя только
    для реально успешно активированных устройств.
    """
    try:
        success, failed, errors = await activate_all_user_devices(user_id)
        if failed > 0:
            await _safe_alert_admin(
                bot,
                f"🚨 Activation failed user={user_id} errors={html.escape(str(errors[:3]))}",
            )
        return success, failed, errors
    except Exception as e:
        await _safe_alert_admin(
            bot,
            f"🚨 CRITICAL activation error user={user_id}: {html.escape(str(e))}",
        )
        return 0, 1, [("activation", str(e))]


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

    if q.total_amount not in PAYMENT_AMOUNTS:
        await bot.answer_pre_checkout_query(
            q.id, ok=False, error_message="Недопустимая сумма платежа"
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

    # 2. Идемпотентно сохраняем факт платежа
    try:
        payment_result = await record_payment_idempotent(
            user_id=user_id,
            payload=payment.invoice_payload,
            telegram_charge_id=payment.telegram_payment_charge_id,
            provider_charge_id=payment.provider_payment_charge_id,
            amount_cents=amount_cents,
        )
    except Exception as e:
        logger.exception("Failed to save payment user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 PAYMENT SAVE FAIL {html.escape(str(e))}")
        return

    # 3. Применяем платёж к балансу ровно один раз.
    # Повторный webhook не должен потерять уже сохранённый, но ещё не применённый платёж.
    try:
        apply_result = await apply_payment_topup_idempotent(
            payment_result["payment"]["id"],
        )
        new_balance = apply_result["balance"]
        if apply_result["already_applied"]:
            logger.info(
                "Payment top-up already applied user=%s provider_charge_id=%s",
                user_id,
                payment.provider_payment_charge_id,
            )
    except Exception as e:
        logger.exception("Balance apply failed user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 PAYMENT APPLY FAIL {html.escape(str(e))}")
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

    legacy_account_uuid = user_data.get("uuid")
    current_status = user_data.get("status", "NEW")

    # 5. Оплата не должна создавать account-level ключ вне `devices`:
    # биллинг считает только `devices`, а users.vless_link/users.uuid — это
    # исторический легаси-путь, который даёт небиллируемый доступ.
    try:
        if current_status not in ("ACTIVE", "TRIAL"):
            await update_user_status(user_id, "ACTIVE")
    except Exception as e:
        logger.exception("User status sync failed after payment user=%s", user_id)
        await _safe_alert_admin(
            bot,
            f"🚨 USER STATUS FAIL user={user_id} err={html.escape(str(e))}",
        )
        await message.answer(
            "⚠️ Оплата прошла, баланс пополнен, но при обновлении статуса произошла ошибка. Мы уже разберёмся.",
            disable_web_page_preview=True,
        )
        return

    try:
        devices = await get_user_devices(user_id)
    except Exception as e:
        logger.exception("Failed to fetch devices after payment user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 DEVICE LIST FAIL user={user_id} err={html.escape(str(e))}")
        return

    # 6. Активируем устройства в панели
    suspended_devices = [
        d for d in devices
        if not d["is_active"] and d.get("disabled_reason") == "insufficient_funds"
    ]
    activation_success = 0
    activation_failed = 0
    if devices:
        activation_success, activation_failed, _ = await _execute_activation(user_id, bot)
        if suspended_devices and activation_failed == 0 and activation_success > 0:
            await set_reactivation_notification_pending(user_id, False)
    elif legacy_account_uuid:
        logger.warning(
            "Payment completed for legacy account-level access user=%s uuid=%s without billable devices",
            user_id,
            legacy_account_uuid,
        )
        await _safe_alert_admin(
            bot,
            f"⚠️ Payment OK but user={user_id} still has legacy account key without billable devices",
        )

    # 7. Отправляем подтверждение — только WebApp кнопка
    if devices:
        if suspended_devices and activation_failed == 0 and activation_success > 0:
            user_text = (
                "🎉 <b>Оплата прошла успешно!</b>\n"
                f"<b>💰 Баланс:</b> {new_balance:.2f} ₽\n"
                "Доступ восстановлен, устройства снова активны. Откройте личный кабинет для управления доступом:"
            )
        elif suspended_devices and activation_failed > 0:
            user_text = (
                "🎉 <b>Оплата прошла успешно!</b>\n"
                f"<b>💰 Баланс:</b> {new_balance:.2f} ₽\n"
                "Баланс пополнен, но часть устройств ещё восстанавливается. Проверьте профиль чуть позже или напишите в поддержку."
            )
        else:
            user_text = (
                "🎉 <b>Оплата прошла успешно!</b>\n"
                f"<b>💰 Баланс:</b> {new_balance:.2f} ₽\n"
                "Баланс обновлён. Откройте личный кабинет для управления доступом:"
            )
    else:
        user_text = (
            "🎉 <b>Оплата прошла успешно!</b>\n"
            f"<b>💰 Баланс:</b> {new_balance:.2f} ₽\n"
            "Чтобы получить VPN-доступ, откройте личный кабинет и добавьте первое устройство."
        )

    await message.answer(
        user_text,
        parse_mode="HTML",
        reply_markup=webapp_button(WEBAPP_URL, user_id),
        disable_web_page_preview=True,
    )

    logger.info(
        "PAYMENT SUCCESS user=%s amount_cents=%s balance=%s devices=%s legacy_account_key=%s",
        user_id, amount_cents, new_balance, len(devices), bool(legacy_account_uuid),
    )

    await _safe_alert_admin(
        bot,
        f"💰 Payment OK user={user_id} amount={amount_cents // 100}р balance={new_balance:.2f}р",
    )
