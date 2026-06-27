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
)
from app.services.vpn import activate_all_user_devices

logger = logging.getLogger(__name__)


def _payment_options_markup() -> types.InlineKeyboardMarkup:
    try:
        amounts = PAYMENT_AMOUNTS
    except NameError:
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
    return builder.as_markup()


async def send_payment_options_message(message: types.Message) -> None:
    """Deep-link entry: /start topup|pay from WebApp."""
    await message.answer(
        "<b>💳 Пополнение баланса</b>\n\nВыберите сумму:",
        parse_mode="HTML",
        reply_markup=_payment_options_markup(),
    )


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


def _build_payment_success_text(
    *,
    new_balance: float,
    has_devices: bool,
    suspended_count: int,
    activation_success: int,
    activation_failed: int,
    post_apply_issues: list[str],
) -> str:
    """User-facing confirmation after balance apply. Post-apply steps are best-effort."""
    header = (
        "🎉 <b>Оплата прошла успешно!</b>\n"
        f"<b>💰 Баланс:</b> {new_balance:.2f} ₽\n"
    )

    if post_apply_issues:
        return (
            f"{header}"
            "Баланс обновлён. Если доступ не восстановился сразу — "
            "откройте личный кабинет или напишите в поддержку."
        )

    if not has_devices:
        return (
            f"{header}"
            "Чтобы получить VPN-доступ, откройте личный кабинет и добавьте первое устройство."
        )

    if suspended_count > 0 and activation_failed == 0 and activation_success > 0:
        return (
            f"{header}"
            "Доступ восстановлен, устройства снова активны. "
            "Откройте личный кабинет для управления доступом:"
        )

    if suspended_count > 0 and activation_failed > 0:
        return (
            f"{header}"
            "Баланс пополнен, но часть устройств ещё восстанавливается. "
            "Проверьте профиль чуть позже или напишите в поддержку."
        )

    return (
        f"{header}"
        "Баланс обновлён. Откройте личный кабинет для управления доступом:"
    )


def _build_payment_apply_pending_text(amount_cents: int) -> str:
    """User message when payment is recorded but balance apply failed after retry."""
    rub = amount_cents / 100
    return (
        "✅ <b>Оплата получена.</b>\n"
        f"Сумма: <b>{rub:.2f} ₽</b>\n\n"
        "Баланс обновится в ближайшее время. Если через несколько минут "
        "средства не появятся в личном кабинете — напишите в поддержку."
    )


_APPLY_RETRY_ATTEMPTS = 2


async def _apply_payment_with_retry(payment_id: int) -> dict:
    """Apply top-up idempotently; one safe retry on transient failure."""
    last_exc: Exception | None = None
    for attempt in range(1, _APPLY_RETRY_ATTEMPTS + 1):
        try:
            result = await apply_payment_topup_idempotent(payment_id)
            if attempt > 1:
                logger.info(
                    "payment.apply_retry_ok payment_id=%s attempt=%s",
                    payment_id,
                    attempt,
                )
            return result
        except Exception as exc:
            last_exc = exc
            logger.exception(
                "payment.apply_failed payment_id=%s attempt=%s/%s",
                payment_id,
                attempt,
                _APPLY_RETRY_ATTEMPTS,
            )
            if attempt >= _APPLY_RETRY_ATTEMPTS:
                break
    assert last_exc is not None
    raise last_exc


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

    await call.message.edit_text(
        "<b>💳 Пополнение баланса</b>\n\nВыберите сумму:",
        parse_mode="HTML",
        reply_markup=_payment_options_markup(),
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

    try:
        await bot.send_invoice(
            chat_id=call.from_user.id,
            title="MetronVPN — пополнение баланса",
            description=f"Пополнение баланса на {rub} руб.",
            payload=payload,
            provider_token=PAY_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=f"Баланс +{rub} руб.", amount=amount_cents)],
        )
    except Exception:
        logger.exception("send_invoice.failed user_id=%s amount_cents=%s", call.from_user.id, amount_cents)
        await call.message.answer("❌ Не удалось выставить счёт. Попробуйте позже.")


@dp.pre_checkout_query(F.invoice_payload.startswith("vpn_pay_"))
async def pre_checkout(q: types.PreCheckoutQuery, bot: Bot) -> None:
    try:
        parts = q.invoice_payload.split("_", 4)
        payload_user_id = int(parts[2])
        expected_amount = int(parts[4])
    except (IndexError, ValueError):
        await bot.answer_pre_checkout_query(
            q.id, ok=False, error_message="Неверный формат платежа"
        )
        return

    if payload_user_id != q.from_user.id:
        logger.warning(
            "pre_checkout.user_mismatch payer=%s payload_user=%s payload=%s",
            q.from_user.id,
            payload_user_id,
            q.invoice_payload,
        )
        await bot.answer_pre_checkout_query(
            q.id, ok=False, error_message="Неверный получатель платежа"
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

    # 3. Применяем платёж к балансу ровно один раз (с одним безопасным retry).
    payment_id = payment_result["payment"]["id"]
    try:
        apply_result = await _apply_payment_with_retry(payment_id)
        new_balance = apply_result["balance"]
        if apply_result["already_applied"]:
            logger.info(
                "Payment top-up already applied user=%s provider_charge_id=%s",
                user_id,
                payment.provider_payment_charge_id,
            )
        if apply_result.get("status_changed"):
            logger.info("Payment restored user status to ACTIVE user=%s", user_id)
    except Exception as e:
        logger.exception(
            "Balance apply failed after retry user=%s payment_id=%s",
            user_id,
            payment_id,
        )
        await _safe_alert_admin(
            bot,
            "🚨 PAYMENT APPLY FAIL "
            f"user={user_id} payment_id={payment_id} "
            f"provider={html.escape(payment.provider_payment_charge_id)} "
            f"err={html.escape(str(e))}",
        )
        try:
            await message.answer(
                _build_payment_apply_pending_text(amount_cents),
                parse_mode="HTML",
                reply_markup=webapp_button(WEBAPP_URL, user_id),
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception(
                "Failed to send payment pending confirmation user=%s payment_id=%s",
                user_id,
                payment_id,
            )
        return

    post_apply_issues: list[str] = []
    user_data: dict | None = None
    devices: list = []
    legacy_account_uuid = None
    suspended_devices: list = []
    activation_success = 0
    activation_failed = 0

    # Post-apply steps are best-effort — balance is already credited.
    try:
        user_data = await get_user_data_dict(user_id)
    except Exception as e:
        logger.exception("Failed to fetch user data user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 USER DATA FAIL {html.escape(str(e))}")
        post_apply_issues.append("user_data")

    if user_data is None and "user_data" not in post_apply_issues:
        logger.error("User data missing after successful balance update user=%s", user_id)
        await _safe_alert_admin(bot, f"🚨 USER DATA MISSING {user_id}")
        post_apply_issues.append("user_data")
    elif user_data:
        legacy_account_uuid = user_data.get("uuid")

        try:
            devices = await get_user_devices(user_id)
        except Exception as e:
            logger.exception("Failed to fetch devices after payment user=%s", user_id)
            await _safe_alert_admin(
                bot,
                f"🚨 DEVICE LIST FAIL user={user_id} err={html.escape(str(e))}",
            )
            post_apply_issues.append("devices")

        if devices:
            suspended_devices = [
                d for d in devices
                if not d["is_active"] and d.get("disabled_reason") == "insufficient_funds"
            ]
            activation_success, activation_failed, _ = await _execute_activation(user_id, bot)
            if suspended_devices and activation_failed == 0 and activation_success > 0:
                try:
                    await set_reactivation_notification_pending(user_id, False)
                except Exception:
                    logger.exception(
                        "Failed to clear reactivation flag after payment user=%s",
                        user_id,
                    )
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

    user_text = _build_payment_success_text(
        new_balance=new_balance,
        has_devices=bool(devices),
        suspended_count=len(suspended_devices),
        activation_success=activation_success,
        activation_failed=activation_failed,
        post_apply_issues=post_apply_issues,
    )

    try:
        await message.answer(
            user_text,
            parse_mode="HTML",
            reply_markup=webapp_button(WEBAPP_URL, user_id),
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("Failed to send payment confirmation user=%s", user_id)
        await _safe_alert_admin(
            bot,
            f"🚨 PAYMENT CONFIRM SEND FAIL user={user_id} balance={new_balance:.2f}",
        )
        return

    logger.info(
        "PAYMENT SUCCESS user=%s amount_cents=%s balance=%s devices=%s legacy_account_key=%s post_apply_issues=%s",
        user_id,
        amount_cents,
        new_balance,
        len(devices),
        bool(legacy_account_uuid),
        post_apply_issues or None,
    )

    await _safe_alert_admin(
        bot,
        f"💰 Payment OK user={user_id} amount={amount_cents // 100}р balance={new_balance:.2f}р",
    )
