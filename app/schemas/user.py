from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_name: str
    is_active: bool
    monthly_cost: float
    daily_cost: float
    vless_link: Optional[str] = None
    created_at: Optional[datetime] = None
    # Reason the device was disabled: 'insufficient_funds' | 'user_request' | None (active)
    # Used by the frontend to render the correct status label.
    disabled_reason: Optional[str] = None


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
    # True when this is the first key issuance for a TRIAL user (no key existed before)
    is_trial_activation: bool = False


class RotateDeviceKeyResponse(BaseModel):
    vless_link: str
    client_uuid: str


class HealthResponse(BaseModel):
    status: str = "ok"


class SupportTicketOut(BaseModel):
    """Одно обращение в поддержку."""
    id: int
    message: str
    status: str
    files: List[Dict[str, Any]] = []
    created_at: datetime
    answered_at: Optional[datetime] = None


class SupportTicketListOut(BaseModel):
    """Список обращений пользователя."""
    tickets: List[SupportTicketOut]
