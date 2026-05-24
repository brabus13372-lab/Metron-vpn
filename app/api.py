import html
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.schemas import (
    DeviceOut,
    DeviceCreateIn,
    UserProfileOut,
    UserBillingOut,
    OkResponse,
    RotateKeyResponse,
    RotateDeviceKeyResponse,
    SupportTicketListOut,
    SupportTicketOut,
    HealthResponse,
)

from app.db import (
    init_db,
    close_db,
    get_user_data_dict,
    get_user_balance,
    get_user_devices,
    get_user_total_monthly_cost,
    remove_device,
    deactivate_device,
    update_user_link,
    add_device,
    get_device_by_id,
    update_device_link,
    create_ticket,
    get_user_tickets,
)
from app.services.vpn import (
    rotate_user_key,
    add_device_to_panel,
    rotate_client_uuid,
    remove_device_from_panel,
)
from app.config import BOT_NAME, ADMIN_ID
from app.bot.bot import bot

logger = logging.getLogger(__name__)

# Maximum number of files per support ticket
_SUPPORT_MAX_FILES = 5
# Maximum single file size: 10 MB
_SUPPORT_MAX_FILE_SIZE = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(title="Metron VPN API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _days_left(expire_at: datetime | None) -> int | None:
    if expire_at is None:
        return None
    now = datetime.now(timezone.utc)
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    return max((expire_at - now).days, 0)


def _build_devices(devices_raw: list[dict]) -> list[DeviceOut]:
    return [
        DeviceOut(
            id=d["id"],
            device_name=d["device_name"],
            is_active=d["is_active"],
            monthly_cost=float(d["monthly_cost"]),
            daily_cost=round(float(d["monthly_cost"]) / 30, 2),
            vless_link=d.get("vless_link"),
            created_at=d.get("created_at"),
        )
        for d in devices_raw
    ]


async def _get_user_or_404(user_id: int) -> dict:
    user = await get_user_data_dict(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health():
    return HealthResponse()


# ---------------------------------------------------------------------------
# Config (публичный, без авторизации)
# ---------------------------------------------------------------------------

@app.get("/api/config", tags=["system"])
async def get_config():
    """Публичные настройки для фронтенда."""
    return {"bot_name": BOT_NAME or ""}


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------

@app.get("/api/user/{user_id}", response_model=UserProfileOut, tags=["profile"])
async def get_user(user_id: int):
    user = await _get_user_or_404(user_id)
    balance = await get_user_balance(user_id)
    monthly_cost = await get_user_total_monthly_cost(user_id)
    devices_raw = await get_user_devices(user_id)
    expire_at = user.get("expire_at")

    return UserProfileOut(
        id=user_id,
        username=user.get("username"),
        status=user.get("status"),
        balance=float(balance or 0),
        monthly_cost=float(monthly_cost or 0),
        daily_cost=round(float(monthly_cost or 0) / 30, 2),
        expire_at=expire_at,
        days_left=_days_left(expire_at),
        vless_link=user.get("vless_link"),
        devices=_build_devices(devices_raw),
    )


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

@app.get("/api/user/{user_id}/devices", response_model=list[DeviceOut], tags=["devices"])
async def get_devices(user_id: int):
    await _get_user_or_404(user_id)
    devices_raw = await get_user_devices(user_id)
    return _build_devices(devices_raw)


@app.post("/api/user/{user_id}/devices", response_model=DeviceOut, tags=["devices"])
async def create_device(user_id: int, body: DeviceCreateIn):
    """Создаёт новый клиент в VPN-панели и сохраняет устройство в БД."""
    user = await _get_user_or_404(user_id)

    if user.get("status") not in ("ACTIVE", "TRIAL"):
        raise HTTPException(status_code=403, detail="User is not active")

    vless_link, client_uuid, err = await add_device_to_panel(
        user_id=user_id,
        username=user.get("username") or f"user_{user_id}",
        device_name=body.device_name,
    )

    if err or not vless_link:
        raise HTTPException(status_code=500, detail=err or "Panel error")

    device_id = await add_device(
        user_id=user_id,
        device_name=body.device_name,
        client_uuid=client_uuid,
        vless_link=vless_link,
    )

    devices_raw = await get_user_devices(user_id)
    device = next((d for d in devices_raw if d["id"] == device_id), None)
    if not device:
        raise HTTPException(status_code=500, detail="Device created but not found")

    return DeviceOut(
        id=device["id"],
        device_name=device["device_name"],
        is_active=device["is_active"],
        monthly_cost=float(device["monthly_cost"]),
        daily_cost=round(float(device["monthly_cost"]) / 30, 2),
        vless_link=device.get("vless_link"),
        created_at=device.get("created_at"),
    )


@app.delete("/api/user/{user_id}/devices/{device_id}", response_model=OkResponse, tags=["devices"])
async def delete_device(user_id: int, device_id: int):
    """Деактивирует устройство (soft delete — is_active=False) и отключает клиент в панели."""
    device = await get_device_by_id(device_id)
    if not device or device["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Device not found")

    # Отключаем клиент в панели только если устройство ещё активно
    if device["is_active"]:
        from app.core.panel_client import update_client_fields
        from app.config import INBOUND_ID
        ok, msg = await update_client_fields(
            device["client_uuid"],
            {"enable": False},
            inbound_id=INBOUND_ID,
        )
        if not ok:
            logger.warning(
                "delete_device.panel_disable_fail device_id=%s uuid=%s msg=%s",
                device_id, device["client_uuid"], msg,
            )
            # Не блокируем — клиент мог уже не существовать в панели

    ok = await deactivate_device(device_id, user_id, reason="user_request")
    if not ok:
        raise HTTPException(status_code=404, detail="Device not found")
    return OkResponse()


@app.delete("/api/user/{user_id}/devices/{device_id}/hard", response_model=OkResponse, tags=["devices"])
async def hard_delete_device(user_id: int, device_id: int):
    """Полное удаление устройства из панели и БД."""
    device = await get_device_by_id(device_id)
    if not device or device["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Device not found")

    panel_ok, panel_err = await remove_device_from_panel(
        client_uuid=device["client_uuid"],
        user_id=user_id,
    )
    if not panel_ok:
        logger.warning(
            "hard_delete_device.panel_fail device_id=%s uuid=%s err=%s",
            device_id, device["client_uuid"], panel_err,
        )
        # Не останавливаемся — remove_device_from_panel толерантен к "not found"

    deleted = await remove_device(device_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Device not found")
    return OkResponse()


@app.post(
    "/api/user/{user_id}/devices/{device_id}/rotate",
    response_model=RotateDeviceKeyResponse,
    tags=["devices"],
)
async def rotate_device_key(user_id: int, device_id: int):
    """Перевыпускает VLESS ключ конкретного устройства."""
    user = await _get_user_or_404(user_id)

    if user.get("status") not in ("ACTIVE", "TRIAL"):
        raise HTTPException(status_code=403, detail="User is not active")

    device = await get_device_by_id(device_id)
    if not device or device["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Device not found")

    if not device["is_active"]:
        raise HTTPException(status_code=409, detail="Device is disabled")

    old_uuid = device["client_uuid"]
    device_label = device["device_name"]

    try:
        new_uuid, new_link, err = await rotate_client_uuid(
            old_uuid=old_uuid,
            user_id=user_id,
            username=device_label,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if err or not new_link:
        raise HTTPException(status_code=500, detail=err or "rotate_client_uuid failed")

    updated = await update_device_link(device_id, user_id, new_uuid, new_link)
    if not updated:
        raise HTTPException(status_code=500, detail="DB update failed after panel rotate")

    return RotateDeviceKeyResponse(vless_link=new_link, client_uuid=new_uuid)


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

@app.get("/api/user/{user_id}/billing", response_model=UserBillingOut, tags=["billing"])
async def get_billing(user_id: int):
    user = await _get_user_or_404(user_id)
    balance = await get_user_balance(user_id)
    monthly_cost = await get_user_total_monthly_cost(user_id)
    expire_at = user.get("expire_at")

    return UserBillingOut(
        balance=float(balance or 0),
        monthly_cost=float(monthly_cost or 0),
        daily_cost=round(float(monthly_cost or 0) / 30, 2),
        expire_at=expire_at,
        days_left=_days_left(expire_at),
    )


# ---------------------------------------------------------------------------
# Rotate key
# ---------------------------------------------------------------------------

@app.post("/api/user/{user_id}/rotate-key", response_model=RotateKeyResponse, tags=["security"])
async def rotate_key(user_id: int):
    """Перевыпускает VLESS ключ пользователя через панель."""
    user = await _get_user_or_404(user_id)

    if user.get("status") not in ("ACTIVE", "TRIAL"):
        raise HTTPException(status_code=403, detail="User is not active")

    try:
        new_link, new_uuid, err = await rotate_user_key(
            user_id=user_id,
            username=user.get("username") or f"user_{user_id}",
            old_uuid=user.get("uuid"),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if err or not new_link:
        raise HTTPException(status_code=500, detail=err or "rotate_user_key failed")

    await update_user_link(user_id, new_link, new_uuid)

    return RotateKeyResponse(vless_link=new_link)


# ---------------------------------------------------------------------------
# Support tickets
# ---------------------------------------------------------------------------

@app.post(
    "/api/user/{user_id}/support",
    response_model=SupportTicketOut,
    tags=["support"],
    status_code=201,
)
async def submit_support_ticket(
    user_id: int,
    message: str = Form(..., min_length=5, max_length=1000),
    files: Optional[List[UploadFile]] = File(default=None),
):
    """
    Принять обращение в поддержку.
    Сохраняет тикет в БД и отправляет админу уведомление с кнопкой "Ответить" через Telegram-бот.
    """
    user = await _get_user_or_404(user_id)

    files_meta: List[Dict[str, Any]] = []
    if files:
        seen_names: set[str] = set()
        for upload in files[:_SUPPORT_MAX_FILES]:
            content = await upload.read()
            if len(content) > _SUPPORT_MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File '{upload.filename}' exceeds 10 MB limit",
                )
            name = upload.filename or "file"
            if name in seen_names:
                continue
            seen_names.add(name)
            files_meta.append({
                "name": name,
                "size": len(content),
                "type": upload.content_type or "application/octet-stream",
            })

    ticket_id = await create_ticket(
        user_id=user_id,
        message=message,
        files=files_meta,
    )

    tickets = await get_user_tickets(user_id, limit=1)
    ticket = next((t for t in tickets if t["id"] == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=500, detail="Ticket created but not found")

    # ── Уведомление админу в Telegram с кнопкой "Ответить" ────────────────────
    if ADMIN_ID:
        username = user.get("username") or f"id{user_id}"
        files_info = ""
        if files_meta:
            names = ", ".join(f["name"] for f in files_meta)
            files_info = f"\n📎 Файлы: {names}"

        notify_text = (
            f"🆘 <b>Новое обращение</b> — через WebApp\n"
            f"👤 @{html.escape(username)} (<code>{user_id}</code>)\n"
            f"🎫 Тикет #{ticket_id}\n\n"
            f"💬 {html.escape(message)}{files_info}"
        )

        # Кнопка "Ответить" — идентична той что уже есть в forward_to_admin в support.py
        # callback_data="reply_{user_id}" → запускает admin_reply_button_handler → FSM состояние
        reply_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Ответить", callback_data=f"reply_{user_id}")]
        ])

        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=notify_text,
                parse_mode="HTML",
                reply_markup=reply_kb,
            )
            logger.info(
                "support notify sent admin=%s ticket=%s user=%s",
                ADMIN_ID, ticket_id, user_id,
            )
        except Exception as exc:
            logger.warning(
                "support notify failed admin=%s ticket=%s: %s",
                ADMIN_ID, ticket_id, exc,
            )
    # ───────────────────────────────────────────────────────────────────────

    return SupportTicketOut(
        id=ticket["id"],
        message=ticket["message"],
        status=ticket["status"],
        files=ticket["files"] if isinstance(ticket["files"], list) else [],
        created_at=ticket["created_at"],
        answered_at=ticket.get("answered_at"),
    )


@app.get(
    "/api/user/{user_id}/support",
    response_model=SupportTicketListOut,
    tags=["support"],
)
async def get_support_tickets(user_id: int):
    """Вернуть историю обращений пользователя."""
    await _get_user_or_404(user_id)

    raw = await get_user_tickets(user_id, limit=20)
    tickets = [
        SupportTicketOut(
            id=t["id"],
            message=t["message"],
            status=t["status"],
            files=t["files"] if isinstance(t["files"], list) else [],
            created_at=t["created_at"],
            answered_at=t.get("answered_at"),
        )
        for t in raw
    ]
    return SupportTicketListOut(tickets=tickets)


# ---------------------------------------------------------------------------
# Config bot_name
# ---------------------------------------------------------------------------

@app.get("/api/config/bot", tags=["config"])
async def get_bot_config():
    from app.config import BOT_NAME
    if not BOT_NAME:
        raise HTTPException(status_code=503, detail="BOT_NAME not configured")
    return {"bot_name": BOT_NAME}


# ---------------------------------------------------------------------------
# Webapp — должен быть последним!
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory="webapp", html=True), name="webapp")
