from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.db import init_db, close_db, get_user_data_dict, get_user_balance, update_user_link
from app.panel_client import PanelClient
from app.config import PANEL_URL, PANEL_USER, PANEL_PASS


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
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


@app.post("/api/user/{user_id}/rotate")
async def rotate_key(user_id: int):
    user = await get_user_data_dict(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    uuid_val = user.get("uuid")
    if not uuid_val:
        raise HTTPException(status_code=400, detail="User has no UUID")

    panel = PanelClient(
        base_url=PANEL_URL,
        username=PANEL_USER,
        password=PANEL_PASS,
    )
    await panel.login()
    new_link = await panel.reset_client_link(uuid_val)
    await update_user_link(user_id, new_link)

    return {"vless_link": new_link}


# Webapp — должен быть последним!
app.mount("/", StaticFiles(directory="webapp", html=True), name="webapp")