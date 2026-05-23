from contextlib import asynccontextmanager
from decimal import Decimal
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
)
from app.services.vpn import rotate_user_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


def _days_left(expire_at: datetime | None) -> int | None:
    if expire_at is None:
        return None
    now = datetime.now(timezone.utc)
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    delta = (expire_at - now).days
    return max(delta, 0)


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

@app.get("/api/user/{user_id}")
async def get_user(user_id: int):
    user = await get_user_data_dict(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    balance = await get_user_balance(user_id)
    monthly_cost = await get_user_total_monthly_cost(user_id)
    devices_raw = await get_user_devices(user_id)

    devices = [
        {
            "id": d["id"],
            "device_name": d["device_name"],
            "is_active": d["is_active"],
            "monthly_cost": float(d["monthly_cost"]),
            "daily_cost": round(float(d["monthly_cost"]) / 30, 2),
            "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
        }
        for d in devices_raw
    ]

    expire_at = user.get("expire_at")

    return {
        "id": user_id,
        "username": user.get("username"),
        "status": user.get("status"),
        "balance": float(balance or 0),
        "monthly_cost": float(monthly_cost or 0),
        "daily_cost": round(float(monthly_cost or 0) / 30, 2),
        "expire_at": expire_at.isoformat() if expire_at else None,
        "days_left": _days_left(expire_at),
        "vless_link": user.get("vless_link"),
        "devices": devices,
    }


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

@app.delete("/api/user/{user_id}/devices/{device_id}")
async def delete_device(user_id: int, device_id: int):
    """Деактивирует устройство (soft delete — is_active=False)."""
    ok = await deactivate_device(device_id, user_id, reason="user_request")
    if not ok:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"ok": True}


@app.delete("/api/user/{user_id}/devices/{device_id}/hard")
async def hard_delete_device(user_id: int, device_id: int):
    """Полное удаление устройства из БД."""
    deleted = await remove_device(device_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Rotate VLESS key
# ---------------------------------------------------------------------------

@app.post("/api/user/{user_id}/rotate-key")
async def rotate_key(user_id: int):
    """Перевыпускает VLESS ключ пользователя через панель."""
    user = await get_user_data_dict(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

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

    return {"ok": True, "vless_link": new_link}


# ---------------------------------------------------------------------------
# Webapp — должен быть последним!
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory="webapp", html=True), name="webapp")
