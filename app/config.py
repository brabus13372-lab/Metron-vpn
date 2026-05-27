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
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

VLESS_PORT = os.getenv("VLESS_PORT", "443")
VLESS_SECURITY = os.getenv("VLESS_SECURITY", "reality")
VLESS_SNI = os.getenv("VLESS_SNI", "google.com")
VLESS_FP = os.getenv("VLESS_FP", "chrome")
VLESS_TYPE = os.getenv("VLESS_TYPE", "tcp")
VLESS_PBK = os.getenv("VLESS_PBK")
VLESS_SID = os.getenv("VLESS_SID")
# REALITY spiderX (path) — для tcp и xhttp
VLESS_SPX = os.getenv("VLESS_SPX", "/")
# XHTTP (type=xhttp): path / host / mode — должны совпадать с inbound в 3x-ui
VLESS_XHTTP_PATH = os.getenv("VLESS_XHTTP_PATH", "/")
VLESS_XHTTP_HOST = os.getenv("VLESS_XHTTP_HOST", "")
VLESS_XHTTP_MODE = os.getenv("VLESS_XHTTP_MODE", "auto")
# Имя после # в ссылке. v2RayTun ломает spx/mode — по умолчанию выключено.
VLESS_USE_FRAGMENT = os.getenv("VLESS_USE_FRAGMENT", "false").lower() in (
    "1",
    "true",
    "yes",
)

PAYMENT_AMOUNT = int(DEVICE_MONTHLY_COST * 100)  # базовая сумма (1 месяц)
PAYMENT_AMOUNTS = [
    PAYMENT_AMOUNT,          # 1 месяц
    PAYMENT_AMOUNT * 2,      # 2 месяца
    PAYMENT_AMOUNT * 3,      # 3 месяца
    PAYMENT_AMOUNT * 6,      # 6 месяцев
]

TZ_NSK = ZoneInfo("Asia/Novosibirsk")
TZ_MSK = ZoneInfo("Europe/Moscow")

TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "1"))

# Максимальный возраст Telegram WebApp initData (сек.). 0 = не проверять срок.
WEBAPP_INIT_MAX_AGE_SEC = int(os.getenv("WEBAPP_INIT_MAX_AGE_SEC", "86400"))

# --- RECONCILE ---
# dev: 300 (5 мин), prod с большим парком: 900 (15 мин)
RECONCILE_INTERVAL_SEC = int(os.getenv("RECONCILE_INTERVAL_SEC", "300"))
