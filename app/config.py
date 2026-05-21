import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from decimal import Decimal

load_dotenv()

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PANEL_URL = os.getenv("PANEL_URL", "").strip().rstrip('/')
PANEL_USER = os.getenv("PANEL_USER")
PANEL_PASS = os.getenv("PANEL_PASS")
SERVER_IP = os.getenv("SERVER_IP")
INBOUND_ID = int(os.getenv("INBOUND_ID", 1))
PAY_TOKEN = os.getenv("PAY_TOKEN")
DEVICE_MONTHLY_COST = Decimal(os.getenv("DEVICE_MONTHLY_COST", "100.00"))
BOT_NAME = os.getenv("BOT_NAME")

VLESS_PORT = os.getenv("VLESS_PORT", "443")
VLESS_SECURITY = os.getenv("VLESS_SECURITY", "reality")
VLESS_SNI = os.getenv("VLESS_SNI", "google.com")
VLESS_FP = os.getenv("VLESS_FP", "chrome")
VLESS_TYPE = os.getenv("VLESS_TYPE", "tcp")
VLESS_PBK = os.getenv("VLESS_PBK")
VLESS_SID = os.getenv("VLESS_SID")

PAYMENT_AMOUNT = int(DEVICE_MONTHLY_COST * 100)

TZ_NSK = ZoneInfo("Asia/Novosibirsk")
TZ_MSK = ZoneInfo("Europe/Moscow")

TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "1"))
