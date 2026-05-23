from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.db import get_user_data_dict, get_user_balance
from datetime import timezone

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

@app.get("/api/user/{user_id}")
async def get_user(user_id: int):
    user = await get_user_data_dict(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    balance = await get_user_balance(user_id)
    expire_at = user.get("expire_at")

    return {
        "id": user_id,
        "username": user.get("username"),
        "status": user.get("status"),
        "balance": float(balance or 0),
        "expire_at": expire_at.isoformat() if expire_at else None,
        "vless_link": user.get("vless_link"),
    }