# app/deps.py
# Dependency stubs — replace with real auth when Telegram WebApp verification is ready.

from fastapi import Header, HTTPException
from app.config import settings


async def verify_internal_token(
    x_internal_token: str = Header(default=""),
) -> None:
    """
    Stub: currently passes all requests through.
    TODO: uncomment the check below when INTERNAL_API_TOKEN is set in .env

    if x_internal_token != settings.internal_api_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    """
    pass
