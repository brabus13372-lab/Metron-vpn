from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_name: str
    is_active: bool
    monthly_cost: float
    daily_cost: float
    created_at: Optional[datetime] = None


class DeviceCreateIn(BaseModel):
    device_name: str


class UserBillingOut(BaseModel):
    balance: float
    monthly_cost: float
    daily_cost: float
    expire_at: Optional[datetime] = None
    days_left: Optional[int] = None


class UserProfileOut(BaseModel):
    id: int
    username: Optional[str] = None
    status: Optional[str] = None
    balance: float
    monthly_cost: float
    daily_cost: float
    expire_at: Optional[datetime] = None
    days_left: Optional[int] = None
    vless_link: Optional[str] = None
    devices: list[DeviceOut] = []


class OkResponse(BaseModel):
    ok: bool = True


class RotateKeyResponse(BaseModel):
    ok: bool = True
    vless_link: Optional[str] = None

class RotateDeviceKeyResponse(BaseModel):
    vless_link: str
    client_uuid: str

class HealthResponse(BaseModel):
    status: str = "ok"
