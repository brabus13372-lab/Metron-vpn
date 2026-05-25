# Metron VPN — Telegram VLESS Bot + WebApp

[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![aiogram](https://img.shields.io/badge/aiogram-3.20-009ddc?logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
[![asyncpg](https://img.shields.io/badge/PostgreSQL-asyncpg-336791?logo=postgresql&logoColor=white)](https://magicstack.github.io/asyncpg/)
[![APScheduler](https://img.shields.io/badge/APScheduler-3.11-orange)](https://apscheduler.readthedocs.io/)
[![3x-ui](https://img.shields.io/badge/panel-3x--ui-red?logo=github)](https://github.com/MHSanaei/3x-ui)
[![YooKassa](https://img.shields.io/badge/payments-YooKassa-8b5cf6)](https://yookassa.ru/)
[![aiohttp](https://img.shields.io/badge/HTTP-aiohttp-2c5bb4)](https://docs.aiohttp.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> **Telegram bot + FastAPI WebApp** for automated VLESS access management via [3x-ui](https://github.com/MHSanaei/3x-ui) panel.  
> Device management, daily billing, key rotation, payments via YooKassa.

🇷🇺 [Русская версия](README.ru.md)

---

## Table of Contents

- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Requirements](#requirements)
- [Installation](#installation)
- [Environment Variables](#environment-variables)
- [API Endpoints](#api-endpoints)
- [Billing](#billing)
- [Security](#security)
- [Maintenance Scripts](#maintenance-scripts)
- [Development](#development)

---

## Architecture

```text
Metron-vpn/
│
├── main.py                          # Entrypoint: uvicorn + aiogram long-polling
│
├── app/
│   ├── core/
│   │   ├── panel_client.py          # aiohttp client for 3x-ui REST API
│   │   ├── logging_sanitizer.py     # Global log filter (masks tokens/cookies)
│   │   └── vless.py                 # VLESS link builder
│   ├── db/
│   │   ├── core.py                  # asyncpg pool + schema verification
│   │   ├── users.py                 # user queries
│   │   ├── devices.py               # device queries
│   │   ├── billing.py               # balance/billing queries
│   │   └── payments.py              # payment idempotency queries
│   ├── schemas/
│   │   └── user.py                  # Pydantic request / response models
│   ├── services/
│   │   ├── billing.py               # BillingEngine: daily charge + TRIAL cycle
│   │   ├── notifications.py         # APScheduler subscription reminders
│   │   ├── reconcile.py             # DB ↔ panel drift repair worker
│   │   └── vpn.py                   # Panel/device business logic
│   └── bot/
│       ├── bot.py                   # aiogram Bot instance
│       ├── dispatcher.py            # Dispatcher + handler registration
│       ├── keyboards.py             # Reply keyboards
│       └── handlers/
│           ├── common.py            # /start, main menu
│           ├── profile.py           # Profile, devices, keys
│           ├── payments.py          # YooKassa: invoice → pre_checkout → success
│           ├── support.py           # FSM ticket system (user → admin)
│           └── admin.py             # Admin commands: broadcast, stats
│
├── webapp/                          # Telegram WebApp (static, served by FastAPI)
│   ├── index.html                   # Entry redirect preserving query string
│   ├── pages/
│   │   ├── profile.html             # Main profile/devices/billing page
│   │   ├── support.html             # Support form + history
│   │   └── protocols.html           # Static protocol info
│   ├── assets/
│   │   ├── css/
│   │   │   ├── base.css
│   │   │   ├── components.css
│   │   │   ├── animations.css
│   │   │   └── pages/               # Page-specific extracted styles
│   │   └── js/
│   │       ├── api.js               # Shared WebApp API client
│   │       └── pages/               # Page-specific extracted scripts
│   └── legacy/
│       └── assets/js/               # Archived stale WebApp modules
│
├── scripts/
│   ├── maintenance/
│   │   └── cleanup_orphan_keys.py   # Canonical orphan account-key cleanup
│   └── migrations/
│       └── migrate_db.py            # Canonical SQLite → PostgreSQL migration
├── cleanup_orphan_keys.py           # Backward-compatible wrapper
├── migrate_db.py                    # Backward-compatible wrapper
├── migrations/                      # Alembic env + versions
├── alembic.ini
├── requirements.txt
├── .env.example
└── .gitignore
```

**Data flows:**
- `Telegram → aiogram handlers → services → db / panel_client`
- `WebApp → FastAPI (api.py) → db / panel_client → 3x-ui`

**Stable production entrypoints:**
- `main.py` — service entrypoint for `systemd`
- `app/` — Python import root
- `webapp/` — static root mounted by FastAPI
- `migrations/` + `alembic.ini` — Alembic runtime root
- Root wrappers `cleanup_orphan_keys.py` and `migrate_db.py` remain supported for operator convenience

---

## Tech Stack

| Layer | Library | Version |
|---|---|---|
| Bot framework | aiogram | 3.20 |
| Web API | FastAPI + uvicorn | 0.115+ |
| HTTP (panel) | aiohttp | latest |
| HTTP (scripts) | httpx | 0.27+ |
| Database | PostgreSQL / asyncpg | latest |
| Scheduler | APScheduler | 3.11 |
| Validation | Pydantic v2 | latest |
| Config | python-dotenv | latest |
| Payments | YooKassa (Telegram Payments) | — |
| Forms/Files | python-multipart | latest |
| Cache | cachetools | latest |
| Timezone | tzdata | latest |

> `urllib3` was removed — it is synchronous and was never actually used.

---

## Requirements

### Panel

The bot works with **[3x-ui](https://github.com/MHSanaei/3x-ui)** — an Xray management panel with REST API.

- A running 3x-ui instance reachable from the bot host
- A configured **VLESS inbound** (REALITY or TLS) with a known `INBOUND_ID`
- Panel credentials (`PANEL_USER` / `PANEL_PASS`)

> Setup guide: [3x-ui Wiki](https://github.com/MHSanaei/3x-ui/wiki)

### Software

- Python **3.10+**
- PostgreSQL (reachable from the bot host)

---

## Installation

```bash
git clone https://github.com/brabus13372-lab/Metron-vpn.git
cd Metron-vpn

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -U pip
pip install -r requirements.txt

cp .env.example .env
# Fill in .env — see Environment Variables section

python3 main.py
```

---

## Environment Variables

All variables are documented in `.env.example`. Required ones:

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `ADMIN_ID` | Telegram user ID of the administrator (integer) |
| `PANEL_URL` | 3x-ui panel base URL (no trailing `/`) |
| `PANEL_USER` | Panel login |
| `PANEL_PASS` | Panel password |
| `INBOUND_ID` | Inbound ID in the panel (integer, usually `1`) |
| `SERVER_IP` | Public server IP (inserted into VLESS links) |
| `DATABASE_URL` | asyncpg DSN: `postgresql://user:pass@host/db` |
| `VLESS_PBK` | REALITY public key |
| `PAY_TOKEN` | YooKassa provider token (obtain via @BotFather) |

> ⚠️ **Never commit `.env`.** The file is excluded in `.gitignore`.

---

## API Endpoints

The WebApp communicates with the bot via REST API (`app/api.py`).

### Profile

| Method | URL | Description |
|---|---|---|
| `GET` | `/api/user/{id}` | Profile: balance, status, devices, key |
| `GET` | `/api/user/{id}/billing` | Billing: balance, cost/day, days remaining |

### Devices

| Method | URL | Description |
|---|---|---|
| `GET` | `/api/user/{id}/devices` | List devices |
| `POST` | `/api/user/{id}/devices` | Create device (panel + DB) |
| `DELETE` | `/api/user/{id}/devices/{dev_id}` | Deactivate device (soft) |
| `DELETE` | `/api/user/{id}/devices/{dev_id}/hard` | Remove from panel and DB (hard) |
| `POST` | `/api/user/{id}/devices/{dev_id}/rotate` | Rotate device key |

### Account Key

| Method | URL | Description |
|---|---|---|
| `POST` | `/api/user/{id}/rotate-key` | Issue / rotate the account key |

> ⚠️ **Guard:** `/rotate-key` returns `409` if the user already has active devices.  
> The account key is intended **only for initial activation** (TRIAL, no devices yet).  
> All subsequent keys are created via `POST /devices`.

### Support

| Method | URL | Description |
|---|---|---|
| `POST` | `/api/user/{id}/support` | Create ticket (text + up to 5 files ≤10 MB) |
| `GET` | `/api/user/{id}/support` | Ticket history (last 20) |

### System

| Method | URL | Description |
|---|---|---|
| `GET` | `/health` | Healthcheck |
| `GET` | `/api/config` | Bot name |

---

## Billing

`app/services/billing.py` — `BillingEngine` powered by APScheduler.

**Logic:**
1. Once a day deducts `SUM(devices.monthly_cost) / 30` from the user's balance
2. On zero balance — suspends the subscription (`status = SUSPENDED`)
3. TRIAL: on expiry — auto-converts to paid or deactivates
4. Notifications: APScheduler sends reminders before access expires

> ⚠️ **Critical:** billing only counts **records in the `devices` table**.  
> The account key (`users.vless_link`) **is not billed**.  
> Never create panel clients bypassing the `devices` table — that is a billing hole.

---

## Security

### Log Masking

`app/logging_sanitizer.py` — global filter, replaces with `[MASKED]`:
- Telegram bot tokens
- `Cookie` / `Set-Cookie` headers
- JSON fields: `password`, `token`, `key`
- Runtime secrets from `app.config`

### SSL

> ⚠️ Requests to 3x-ui are made with `verify=False` (aiohttp) for self-signed certificates.  
> If your panel has a valid CA certificate — remove `ssl=False` in `app/core/panel_client.py`.

### Server-Side Guards

| Endpoint | Code | Condition |
|---|---|---|
| `POST /rotate-key` | `409` | User already has active devices |
| `DELETE /devices/{id}` | `409` | Deleting the last active device |
| `POST /devices` | `403` | User status is not `ACTIVE` / `TRIAL` |

> All guards are enforced server-side — WebApp client checks are not the only protection.

### Git Hygiene

`.gitignore` excludes: `.env`, `*.db*`, venv, caches, logs, TLS keys/certificates.

---

## Maintenance Scripts

### `cleanup_orphan_keys.py`

**One-time migration script.** Removes "ghost" account clients from the 3x-ui panel — those created via `/rotate-key` for users who have already migrated to `devices`.

**When to run:** once, immediately after deploying the guard on `/rotate-key`.

> ⚠️ Always verify with `--dry-run` before `--apply`.

```bash
# Dry-run: show what would be deleted (no changes).
# Backward-compatible root wrapper:
export $(grep -v '^#' .env | xargs) && python cleanup_orphan_keys.py --dry-run

# Canonical script location:
export $(grep -v '^#' .env | xargs) && .venv/bin/python -m scripts.maintenance.cleanup_orphan_keys --dry-run

# Apply: run the cleanup.
# Backward-compatible root wrapper:
export $(grep -v '^#' .env | xargs) && python cleanup_orphan_keys.py --apply

# Canonical script location:
export $(grep -v '^#' .env | xargs) && .venv/bin/python -m scripts.maintenance.cleanup_orphan_keys --apply
```

The script is **idempotent** — re-running is safe. If the panel is unreachable for a UUID, the DB is not touched and the entry will be retried on the next run.

### `migrate_db.py`

One-time data migration from SQLite → PostgreSQL (used during the production database transition). Not needed for new installations.

Canonical implementation: `.venv/bin/python -m scripts.migrations.migrate_db`

Backward-compatible wrapper: `python migrate_db.py`

---

## Development

### Add a Handler

1. Create a file in `app/bot/handlers/`
2. Use `dp` from `app.bot.dispatcher`, register with decorators
3. Import the file in `app/bot/dispatcher.py`

### Add a Service

1. Place business logic in `app/services/`
2. Keep Telegram-specific code in handlers — services are testable independently

### Add an API Endpoint

1. Add the endpoint in `app/api.py`
2. Add Pydantic schemas in `app/schemas.py`
3. SQL queries go only in `app/db.py` (no inline SQL in `api.py`)

### Logging

Log freely — `logging_sanitizer.py` will automatically mask secrets.

---

## Disclaimer

Provided as-is for educational and operational automation purposes. Responsibility for infrastructure, server security, and lawful use lies with the operator.
