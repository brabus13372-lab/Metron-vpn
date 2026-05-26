"""
Metron VPN — FastAPI entry point.
"""
import html
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from time import monotonic
from typing import Any, Dict, List, Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
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
from app.services.billing import billing_engine
from app.config import BOT_NAME, ADMIN_ID
from app.bot.bot import bot

logger = logging.getLogger(__name__)

_SUPPORT_MAX_FILES = 5
_SUPPORT_MAX_FILE_SIZE = 10 * 1024 * 1024
_ADMIN_USER_ERROR_COOLDOWN_SEC = 120.0
_admin_user_error_last_sent: dict[tuple[Any, ...], float] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    billing_engine.set_bot(bot)
    billing_engine.start()
    logger.info("Billing engine started")
    yield
    billing_engine.stop()
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

def _calendar_days_left(expire_at: datetime | None) -> int | None:
    if expire_at is None:
        return None
    now = datetime.now(timezone.utc)
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    return max((expire_at - now).days, 0)


def _billing_days_left(balance_rub: float | Decimal | None, monthly_cost_rub: float | Decimal | None) -> int:
    balance = Decimal(str(balance_rub or 0))
    monthly_cost = Decimal(str(monthly_cost_rub or 0))
    if balance <= 0 or monthly_cost <= 0:
        return 0
    daily_cost = monthly_cost / Decimal("30")
    if daily_cost <= 0:
        return 0
    return max(int(balance / daily_cost), 0)


def _effective_user_status(
    raw_status: str | None,
    balance_rub: float | Decimal | None,
    devices_raw: list[dict],
) -> str | None:
    if raw_status != "ACTIVE":
        return raw_status
    if any(d.get("is_active") for d in devices_raw):
        return raw_status
    if Decimal(str(balance_rub or 0)) <= 0:
        return "EXPIRED"
    return raw_status


def _profile_days_left(
    status: str | None,
    expire_at: datetime | None,
    balance_rub: float | Decimal | None,
    monthly_cost_rub: float | Decimal | None,
) -> int | None:
    if status == "TRIAL":
        return _calendar_days_left(expire_at)
    return _billing_days_left(balance_rub, monthly_cost_rub)


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
            disabled_reason=d.get("disabled_reason"),
        )
        for d in devices_raw
    ]


def _device_state_summary(devices_raw: list[dict]) -> str:
    active = sum(1 for d in devices_raw if d.get("is_active"))
    inactive = sum(1 for d in devices_raw if not d.get("is_active"))
    reasons = sorted({
        str(d.get("disabled_reason") or "none")
        for d in devices_raw
        if not d.get("is_active")
    })
    reasons_text = ", ".join(reasons) if reasons else "none"
    return f"active={active}, inactive={inactive}, reasons={reasons_text}"


async def _notify_admin_user_error(
    *,
    action: str,
    user_id: int | None,
    detail: str,
    status_code: int,
    extra: str | None = None,
) -> None:
    if not ADMIN_ID:
        return

    clean_detail = str(detail).strip() or "unknown error"
    clean_extra = (extra or "").strip()
    dedupe_key = (
        action,
        user_id,
        status_code,
        clean_detail[:160],
        clean_extra[:160],
    )
    now = monotonic()
    last_sent = _admin_user_error_last_sent.get(dedupe_key)
    if last_sent is not None and now - last_sent < _ADMIN_USER_ERROR_COOLDOWN_SEC:
        return
    _admin_user_error_last_sent[dedupe_key] = now

    text = (
        "⚠️ <b>Ошибка пользователя в WebApp</b>\n"
        f"Action: <code>{html.escape(action)}</code>\n"
        f"User: <code>{user_id if user_id is not None else 'unknown'}</code>\n"
        f"HTTP: <code>{status_code}</code>\n"
        f"Detail: {html.escape(clean_detail)}"
    )
    if clean_extra:
        text += f"\nContext: <code>{html.escape(clean_extra[:1200])}</code>"

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=text,
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("admin user error notify failed action=%s user=%s err=%s", action, user_id, exc)


async def _raise_user_action_error(
    *,
    action: str,
    user_id: int | None,
    status_code: int,
    detail: str,
    extra: str | None = None,
) -> None:
    await _notify_admin_user_error(
        action=action,
        user_id=user_id,
        detail=detail,
        status_code=status_code,
        extra=extra,
    )
    raise HTTPException(status_code=status_code, detail=detail)


async def _get_user_or_404(user_id: int) -> dict:
    user = await get_user_data_dict(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _assert_not_last_active_device(user_id: int, device_id: int) -> None:
    """
    Raises HTTP 409 if `device_id` is the only active device for `user_id`.

    This is a server-side safeguard that mirrors the client-side lock in
    profile.html.  Without it, a direct API call could remove the last device
    while billing still tries to charge the user, or leave a ghost client in
    the panel with no corresponding DB record.
    """
    all_devices = await get_user_devices(user_id)
    active_ids = [d["id"] for d in all_devices if d.get("is_active")]
    if active_ids == [device_id]:
        raise HTTPException(
            status_code=409,
            detail="Cannot remove the last active device. Disable your subscription first.",
        )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health():
    return HealthResponse()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@app.get("/api/config", tags=["system"])
async def get_config():
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
    balance_rub = float(balance or 0)
    monthly_cost_rub = float(monthly_cost or 0)
    effective_status = _effective_user_status(user.get("status"), balance_rub, devices_raw)

    return UserProfileOut(
        id=user_id,
        username=user.get("username"),
        status=effective_status,
        balance=balance_rub,
        monthly_cost=monthly_cost_rub,
        daily_cost=round(monthly_cost_rub / 30, 2),
        expire_at=expire_at,
        days_left=_profile_days_left(effective_status, expire_at, balance_rub, monthly_cost_rub),
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
    user = await _get_user_or_404(user_id)
    balance = await get_user_balance(user_id)
    devices_raw = await get_user_devices(user_id)
    effective_status = _effective_user_status(user.get("status"), float(balance or 0), devices_raw)

    if effective_status not in ("ACTIVE", "TRIAL"):
        await _raise_user_action_error(
            action="create_device",
            user_id=user_id,
            status_code=403,
            detail="User is not active",
            extra=(
                f"raw_status={user.get('status')}, effective_status={effective_status}, "
                f"balance={float(balance or 0):.2f}, {_device_state_summary(devices_raw)}"
            ),
        )

    vless_link, client_uuid, err = await add_device_to_panel(
        user_id=user_id,
        username=user.get("username") or f"user_{user_id}",
        device_name=body.device_name,
    )

    if err or not vless_link:
        await _raise_user_action_error(
            action="create_device",
            user_id=user_id,
            status_code=500,
            detail=err or "Panel error",
            extra=f"device_name={body.device_name}, raw_status={user.get('status')}",
        )

    device_id = await add_device(
        user_id=user_id,
        device_name=body.device_name,
        client_uuid=client_uuid,
        vless_link=vless_link,
    )

    devices_raw = await get_user_devices(user_id)
    device = next((d for d in devices_raw if d["id"] == device_id), None)
    if not device:
        await _raise_user_action_error(
            action="create_device",
            user_id=user_id,
            status_code=500,
            detail="Device created but not found",
            extra=f"device_id={device_id}, client_uuid={client_uuid}",
        )

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
    device = await get_device_by_id(device_id)
    if not device or device["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Device not found")

    # Server-side guard: cannot deactivate the last active device.
    await _assert_not_last_active_device(user_id, device_id)

    if device["is_active"]:
        from app.core.panel_client import update_client_fields
        from app.config import INBOUND_ID
        ok, msg = await update_client_fields(
            device["client_uuid"],
            {"enable": False},
            inbound_id=INBOUND_ID,
        )
        if not ok:
            logger.error(
                "delete_device.panel_disable_fail device_id=%s uuid=%s msg=%s",
                device_id, device["client_uuid"], msg,
            )
            await _raise_user_action_error(
                action="delete_device",
                user_id=user_id,
                status_code=502,
                detail=f"Panel error — device not disabled: {msg}",
                extra=f"device_id={device_id}, client_uuid={device['client_uuid']}",
            )

    ok = await deactivate_device(device_id, user_id, reason="user_request")
    if not ok:
        raise HTTPException(status_code=404, detail="Device not found")
    return OkResponse()


@app.delete("/api/user/{user_id}/devices/{device_id}/hard", response_model=OkResponse, tags=["devices"])
async def hard_delete_device(user_id: int, device_id: int):
    device = await get_device_by_id(device_id)
    if not device or device["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Device not found")

    # Server-side guard: cannot hard-delete the last active device.
    await _assert_not_last_active_device(user_id, device_id)

    panel_ok, panel_err = await remove_device_from_panel(
        client_uuid=device["client_uuid"],
        user_id=user_id,
    )
    if not panel_ok:
        logger.error(
            "hard_delete_device.panel_fail device_id=%s uuid=%s err=%s",
            device_id, device["client_uuid"], panel_err,
        )
        await _raise_user_action_error(
            action="hard_delete_device",
            user_id=user_id,
            status_code=502,
            detail=f"Panel error — device not deleted: {panel_err}",
            extra=f"device_id={device_id}, client_uuid={device['client_uuid']}",
        )

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
    user = await _get_user_or_404(user_id)
    balance = await get_user_balance(user_id)
    effective_status = _effective_user_status(
        user.get("status"),
        float(balance or 0),
        await get_user_devices(user_id),
    )

    if effective_status not in ("ACTIVE", "TRIAL"):
        await _raise_user_action_error(
            action="rotate_device_key",
            user_id=user_id,
            status_code=403,
            detail="User is not active",
            extra=(
                f"raw_status={user.get('status')}, effective_status={effective_status}, "
                f"balance={float(balance or 0):.2f}"
            ),
        )

    device = await get_device_by_id(device_id)
    if not device or device["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Device not found")

    if not device["is_active"]:
        await _raise_user_action_error(
            action="rotate_device_key",
            user_id=user_id,
            status_code=409,
            detail="Device is disabled",
            extra=(
                f"device_id={device_id}, client_uuid={device['client_uuid']}, "
                f"disabled_reason={device.get('disabled_reason')}"
            ),
        )

    old_uuid = device["client_uuid"]
    device_label = device["device_name"]

    try:
        new_uuid, new_link, err = await rotate_client_uuid(
            old_uuid=old_uuid,
            user_id=user_id,
            username=device_label,
        )
    except Exception as exc:
        await _raise_user_action_error(
            action="rotate_device_key",
            user_id=user_id,
            status_code=500,
            detail=str(exc),
            extra=f"device_id={device_id}, old_uuid={old_uuid}",
        )

    if err or not new_link:
        await _raise_user_action_error(
            action="rotate_device_key",
            user_id=user_id,
            status_code=500,
            detail=err or "rotate_client_uuid failed",
            extra=f"device_id={device_id}, old_uuid={old_uuid}",
        )

    updated = await update_device_link(device_id, user_id, new_uuid, new_link)
    if not updated:
        await _raise_user_action_error(
            action="rotate_device_key",
            user_id=user_id,
            status_code=500,
            detail="DB update failed after panel rotate",
            extra=f"device_id={device_id}, old_uuid={old_uuid}, new_uuid={new_uuid}",
        )

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
    balance_rub = float(balance or 0)
    monthly_cost_rub = float(monthly_cost or 0)
    effective_status = _effective_user_status(
        user.get("status"),
        balance_rub,
        await get_user_devices(user_id),
    )

    return UserBillingOut(
        balance=balance_rub,
        monthly_cost=monthly_cost_rub,
        daily_cost=round(monthly_cost_rub / 30, 2),
        expire_at=expire_at,
        days_left=_profile_days_left(effective_status, expire_at, balance_rub, monthly_cost_rub),
    )


# ---------------------------------------------------------------------------
# Rotate key (account-level key — users.vless_link)
#
# GUARD: account-level key issuance is legacy and allowed only for TRIAL
# first-activation. Every long-lived key must live in `devices` so billing can
# see it. This eliminates the 100 ₽/mo leak where a panel client was created
# here but never charged.
# ---------------------------------------------------------------------------

@app.post("/api/user/{user_id}/rotate-key", response_model=RotateKeyResponse, tags=["security"])
async def rotate_key(user_id: int):
    user = await _get_user_or_404(user_id)
    status = user.get("status")

    if status not in ("ACTIVE", "TRIAL"):
        await _raise_user_action_error(
            action="rotate_key",
            user_id=user_id,
            status_code=403,
            detail="Subscription is not active. Please top up your balance.",
            extra=f"raw_status={status}",
        )

    devices = await get_user_devices(user_id)
    if devices:
        await _raise_user_action_error(
            action="rotate_key",
            user_id=user_id,
            status_code=409,
            detail=(
                "You already have device records. "
                "Use per-device keys from the Devices section. "
                "The account-level key is only for first-time TRIAL activation."
            ),
            extra=_device_state_summary(devices),
        )

    if status != "TRIAL":
        await _raise_user_action_error(
            action="rotate_key",
            user_id=user_id,
            status_code=409,
            detail=(
                "The account-level key is only available for first-time TRIAL activation. "
                "Open the Devices section and create your first device instead."
            ),
            extra=f"raw_status={status}, had_key_before={bool(user.get('vless_link'))}",
        )

    had_key_before = bool(user.get("vless_link"))

    try:
        new_link, new_uuid, err = await rotate_user_key(
            user_id=user_id,
            username=user.get("username") or f"user_{user_id}",
            old_uuid=user.get("uuid"),
        )
    except Exception as exc:
        await _raise_user_action_error(
            action="rotate_key",
            user_id=user_id,
            status_code=500,
            detail=str(exc),
            extra=f"old_uuid={user.get('uuid')}",
        )

    if err or not new_link:
        await _raise_user_action_error(
            action="rotate_key",
            user_id=user_id,
            status_code=500,
            detail=err or "rotate_user_key failed",
            extra=f"old_uuid={user.get('uuid')}",
        )

    await update_user_link(user_id, new_link, new_uuid)

    is_trial_activation = (status == "TRIAL" and not had_key_before)

    return RotateKeyResponse(vless_link=new_link, is_trial_activation=is_trial_activation)


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
    user = await _get_user_or_404(user_id)

    files_meta: List[Dict[str, Any]] = []
    if files:
        seen_names: set[str] = set()
        for upload in files[:_SUPPORT_MAX_FILES]:
            content = await upload.read()
            if len(content) > _SUPPORT_MAX_FILE_SIZE:
                await _raise_user_action_error(
                    action="submit_support_ticket",
                    user_id=user_id,
                    status_code=413,
                    detail=f"File '{upload.filename}' exceeds 10 MB limit",
                    extra=f"filename={upload.filename}, size={len(content)}",
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
        await _raise_user_action_error(
            action="submit_support_ticket",
            user_id=user_id,
            status_code=500,
            detail="Ticket created but not found",
            extra=f"ticket_id={ticket_id}",
        )

    if ADMIN_ID:
        username = user.get("username") or f"id{user_id}"
        files_info = ""
        if files_meta:
            names = ", ".join(f["name"] for f in files_meta)
            files_info = f"\n\U0001f4ce Файлы: {names}"

        notify_text = (
            f"\U0001f198 <b>Новое обращение</b> — через WebApp\n"
            f"\U0001f464 @{html.escape(username)} (<code>{user_id}</code>)\n"
            f"\U0001f3ab Тикет #{ticket_id}\n\n"
            f"\U0001f4ac {html.escape(message)}{files_info}"
        )

        reply_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="\u270d\ufe0f Ответить", callback_data=f"reply_{user_id}")]
        ])

        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=notify_text,
                parse_mode="HTML",
                reply_markup=reply_kb,
            )
        except Exception as exc:
            logger.warning("support notify failed admin=%s ticket=%s: %s", ADMIN_ID, ticket_id, exc)

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

def _legacy_webapp_redirect_target(request: Request) -> str:
    page_name = request.url.path.rsplit("/", 1)[-1]
    prefix = "/webapp/pages" if request.url.path.startswith("/webapp/") else "/pages"
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{prefix}/{page_name}{query}"


@app.get("/profile.html", include_in_schema=False)
@app.get("/support.html", include_in_schema=False)
@app.get("/protocols.html", include_in_schema=False)
@app.get("/webapp/profile.html", include_in_schema=False)
@app.get("/webapp/support.html", include_in_schema=False)
@app.get("/webapp/protocols.html", include_in_schema=False)
async def redirect_legacy_webapp_page(request: Request):
    return RedirectResponse(
        url=_legacy_webapp_redirect_target(request),
        status_code=307,
    )


app.mount("/webapp", StaticFiles(directory="webapp", html=True), name="webapp-legacy")
app.mount("/", StaticFiles(directory="webapp", html=True), name="webapp")
