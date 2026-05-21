# METRON_VPN — Telegram VLESS/V2Ray Bot Manager

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> High performance. Maximum privacy. Zero nonsense.

Telegram-бот для автоматизации выдачи VLESS-доступа через панель **3x-ui**: создание ключей, ежедневный биллинг, продление подписки, ротация ключей, уведомления и поддержка пользователей.

---

## Features

- **3x-ui Panel API** — create / renew / deactivate VLESS clients
- **VLESS/REALITY link generator** — ready-to-import links for users
- **Telegram Payments (RUB)** — invoice + payment + balance top-up
- **Daily billing engine** — atomic per-day charge, auto-deactivation on zero balance
- **Trial period** — configurable free trial (default: 1 day), auto-expiry cycle
- **Expiration notifications** — proactive reminders before access expires
- **FSM support tickets** — user → admin ticket flow with inline reply
- **Global log sanitizer** — tokens / cookies / passwords masked as `[MASKED]` across the entire logging pipeline

---

## Architecture

```text
.
├── app/
│   ├── config.py              # env config (dotenv) + constants
│   ├── db.py                  # PostgreSQL helpers (asyncpg)
│   ├── vless.py               # VLESS link builder
│   ├── panel_client.py        # aiohttp client: login, CSRF, panel requests
│   ├── logging_sanitizer.py   # global logging filter (mask secrets)
│   ├── services/
│   │   ├── billing.py         # BillingEngine: daily charge + trial expiry (APScheduler)
│   │   ├── vpn.py             # create / rotate / deactivate logic
│   │   └── notifications.py   # scheduled expiry notifications
│   └── bot/
│       ├── dispatcher.py      # aiogram Dispatcher + handler registration
│       ├── keyboards.py       # reply keyboards
│       └── handlers/          # handlers grouped by domain
│           ├── common.py
│           ├── profile.py
│           ├── payments.py
│           ├── support.py
│           └── admin.py
├── main.py                    # entrypoint: logging → db → scheduler → polling
├── migrate_db.py              # migration helper (SQLite → PostgreSQL)
├── requirements.txt
├── .env.example               # environment variable template
└── .gitignore
```

**Handlers** → Telegram UX. **Services** → business logic. **Core** (`panel_client`, `db`, `config`) → infrastructure.

---

## Tech Stack

| Layer | Library / Tool |
|---|---|
| Bot framework | aiogram 3.x |
| Async HTTP | aiohttp |
| Database | PostgreSQL (asyncpg) |
| Scheduling | APScheduler 3.x |
| Config | python-dotenv |
| Payments | Telegram Payments API (RUB) |

---

## Security

### Log Sanitizer

All logging output passes through `app/logging_sanitizer.py`, which masks:

- Telegram bot tokens
- `Cookie` / `Set-Cookie` headers
- JSON fields: `password`, `token`, `key`
- Any runtime secrets loaded from `app.config`

Everything is replaced with **`[MASKED]`** — no partial leaks.

### SSL Note

> ⚠️ The bot disables SSL certificate verification for 3x-ui panel requests (`urllib3.disable_warnings` + `verify=False`). This is intentional for setups where the panel uses a **self-signed certificate**. If your panel has a valid CA-signed cert, remove the `verify=False` flag in `panel_client.py` for better security.

### Git Hygiene

`.gitignore` excludes: `.env`, `*.db*`, venvs, caches, logs, TLS keys/certs.

---

## Installation

### Requirements

- Python 3.10+
- PostgreSQL (accessible from the bot host)
- A running **3x-ui panel** with a configured **VLESS inbound**

### Clone & setup

```bash
git clone https://github.com/brabus13372-lab/Vpn-project.git
cd Vpn-project

python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
```

### Configure `.env`

```bash
cp .env.example .env
# Edit .env and fill in all required variables
```

Required variables (see `.env.example` for full list with comments):

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `ADMIN_ID` | Your Telegram numeric user ID |
| `PANEL_URL` | 3x-ui panel base URL (no trailing slash) |
| `PANEL_USER` / `PANEL_PASS` | Panel credentials |
| `SERVER_IP` | Public IP used in VLESS links |
| `PAY_TOKEN` | Telegram Payments provider token |
| `DATABASE_URL` | PostgreSQL connection string |
| `VLESS_PBK` | REALITY public key |

### Run

```bash
python3 main.py
```

---

## Bot Commands & Flows

| Command / Button | Description |
|---|---|
| `/start` | Main menu |
| 🚀 Подключить VPN | Create trial key (24h) if no active key |
| 👤 Мой профиль | View subscription status, balance, devices |
| 🔄 Обновить ключ | Rotate UUID (invalidate old, issue new) |
| 💳 Пополнить баланс | Top-up balance via Telegram Payments |
| 🆘 Поддержка | FSM ticket to admin with inline reply |

---

## Development

### Add a new handler

1. Create a file under `app/bot/handlers/` (e.g. `foo.py`).
2. Import `dp` from `app.bot.dispatcher` and register handlers with decorators.
3. Import the file in `app/bot/dispatcher.py` so decorators execute on startup.

### Add a new service

1. Put business logic in `app/services/`.
2. Keep Telegram-specific code inside handlers — services should be independently testable.

### Logging

Log freely — the global sanitizer in `main.py` and `dispatcher.py` will mask secrets automatically.

---

## Disclaimer

Provided as-is for educational and operational automation purposes. You are responsible for your own infrastructure, server security, and lawful use.
