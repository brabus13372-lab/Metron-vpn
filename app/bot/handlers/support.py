import html
import logging
import time
from typing import Dict

from aiogram import Bot, types, F
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.bot.dispatcher import dp
from app.config import ADMIN_ID, BOT_NAME

logger = logging.getLogger(__name__)

SUPPORT_COOLDOWN = 10.0
TG_TEXT_LIMIT = 4096
TG_CAPTION_LIMIT = 1024

_last_support_message: Dict[int, float] = {}

class SupportState(StatesGroup):
    waiting_for_question = State()
    waiting_for_admin_reply = State()

@dp.message(F.text == "🆘 Поддержка")
async def support_cmd(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(SupportState.waiting_for_question)
    await message.answer(
        "✍️ Пожалуйста, опишите ваш вопрос или отправьте медиафайл.\n"
        "Обращение будет передано администратору.\n\n"
        "Для отмены напишите /cancel"
    )

@dp.message(SupportState.waiting_for_question, F.text == "/cancel")
@dp.message(SupportState.waiting_for_admin_reply, F.text == "/cancel")
async def cancel_support(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Обращение отменено.")

@dp.message(SupportState.waiting_for_question)
async def forward_to_admin(message: types.Message, state: FSMContext, bot: Bot) -> None:
    user_id = message.from_user.id

    now = time.monotonic()
    last = _last_support_message.get(user_id, 0.0)
    if now - last < SUPPORT_COOLDOWN:
        return await message.answer(
            "⏳ Пожалуйста, подождите 10 секунд перед отправкой следующего обращения."
        )

    raw_username = message.from_user.username
    username_text = f"@{html.escape(raw_username)}" if raw_username else "Без username"
    full_name = html.escape(message.from_user.full_name or "Без имени")
    content_text = html.escape(message.text or message.caption or "[Медиафайл без описания]")

    header = _build_support_header(user_id, full_name, username_text)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Ответить", callback_data=f"reply_{user_id}")]
    ])

    await state.clear()

    try:
        if message.text:
            full_text = _cut_text(f"{header}{content_text}", TG_TEXT_LIMIT)
            await bot.send_message(
                ADMIN_ID,
                full_text,
                parse_mode="HTML",
                reply_markup=kb,
            )

        elif message.photo:
            caption = _cut_text(f"{header}{content_text}", TG_CAPTION_LIMIT)
            await bot.send_photo(
                ADMIN_ID,
                message.photo[-1].file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )

        elif message.video:
            caption = _cut_text(f"{header}{content_text}", TG_CAPTION_LIMIT)
            await bot.send_video(
                ADMIN_ID,
                message.video.file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )

        elif message.document:
            caption = _cut_text(f"{header}{content_text}", TG_CAPTION_LIMIT)
            await bot.send_document(
                ADMIN_ID,
                message.document.file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )

        elif message.voice:
            caption = _cut_text(f"{header}{content_text}", TG_CAPTION_LIMIT)
            await bot.send_voice(
                ADMIN_ID,
                message.voice.file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )

        elif message.video_note:
            await bot.send_video_note(
                ADMIN_ID,
                message.video_note.file_id,
            )
            await bot.send_message(
                ADMIN_ID,
                _cut_text(f"{header}{content_text}", TG_TEXT_LIMIT),
                parse_mode="HTML",
                reply_markup=kb,
            )

        else:
            await bot.copy_message(
                ADMIN_ID,
                message.chat.id,
                message.message_id,
                reply_markup=kb,
            )

        _last_support_message[user_id] = now

        await message.answer("✅ Ваше обращение отправлено. Ожидайте ответа.")
        logger.info("Support request forwarded to admin: user_id=%s", user_id)

    except Exception:
        logger.exception("Support forward failed: user_id=%s", user_id)
        await message.answer("❌ Произошла ошибка при отправке. Попробуйте позже.")

def _cut_text(text: str, limit: int) -> str:
    if len(text) > limit:
        return text[:limit - 3] + "..."
    return text


def _build_support_header(user_id: int, full_name: str, username_text: str) -> str:
    return (
        f"📩 <b>НОВОЕ ОБРАЩЕНИЕ</b>\n"
        f"От: {full_name} ({username_text})\n"
        f"ID: <code>{user_id}</code>\n\n"
        f"📝 Текст: "
    )


@dp.callback_query(F.data.startswith("reply_"))
async def admin_reply_button_handler(call: types.CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Недостаточно прав", show_alert=True)

    try:
        target_user_id = int(call.data.split("_")[1])
    except (IndexError, ValueError):
        return await call.answer("❌ Ошибка данных callback", show_alert=True)

    await state.update_data(target_user_id=target_user_id)
    await state.set_state(SupportState.waiting_for_admin_reply)
    await call.message.answer(
        f"✍️ Отправьте ответ для пользователя <code>{target_user_id}</code>:\n"
        f"(текст, фото, видео, голосовое, кружок или документ)\n\n"
        f"Для отмены напишите /cancel",
        parse_mode="HTML"
    )
    await call.answer("Режим ответа активирован")


@dp.message(SupportState.waiting_for_admin_reply, F.from_user.id == ADMIN_ID)
async def send_admin_reply_to_user(message: types.Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    target_user_id = data.get("target_user_id")

    if not target_user_id:
        await state.clear()
        return await message.answer("❌ ID пользователя потерян.")

    try:
        await bot.send_message(target_user_id, f"✉️ <b>Ответ от поддержки {BOT_NAME}:</b>", parse_mode="HTML")
        await bot.copy_message(
            chat_id=target_user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id
        )
        await message.answer(
            f"✅ Ответ успешно отправлен пользователю <code>{target_user_id}</code>",
            parse_mode="HTML"
        )
        logger.info("Администратор отправил ответ пользователю %s", target_user_id)

    except Exception:
        logger.exception("Ошибка отправки ответа пользователю %s", target_user_id)
        await message.answer("❌ Ошибка при отправке. Возможно, пользователь заблокировал бота.")

    finally:
        await state.clear()