from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.schemas import (
    DeviceOut,
    DeviceCreateIn,
    UserProfileOut,
    UserBillingOut,
    OkResponse,
    RotateKeyResponse,
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
)
from app.services.vpn import rotate_user_key, add_device_to_panel


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
        created_at=device.get("created_at"),
    )


@app.delete("/api/user/{user_id}/devices/{device_id}", response_model=OkResponse, tags=["devices"])
async def delete_device(user_id: int, device_id: int):
    """Деактивирует устройство (soft delete — is_active=False)."""
    ok = await deactivate_device(device_id, user_id, reason="user_request")
    if not ok:
        raise HTTPException(status_code=404, detail="Device not found")
    return OkResponse()


@app.delete("/api/user/{user_id}/devices/{device_id}/hard", response_model=OkResponse, tags=["devices"])
async def hard_delete_device(user_id: int, device_id: int):
    """Полное удаление устройства из БД."""
    deleted = await remove_device(device_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Device not found")
    return OkResponse()


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
# Webapp — должен быть последним!
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory="webapp", html=True), name="webapp")
