
## METRON_VPN — Advanced VLESS/V2Ray Telegram Bot Manager

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)

High performance. Maximum privacy. Zero nonsense.

METRON_VPN is a Telegram bot that automates VLESS access provisioning via a 3x-ui panel, handles renewals/payments, rotates compromised keys, and proactively notifies users before expiration — with **global sensitive-data masking in logs**.

---

## Key Features

- **3x-ui Panel Integration (API)**: create/renew/delete clients via panel endpoints.
- **VLESS/REALITY Link Generator**: produces ready-to-import VLESS links for end users.
- **Telegram Payments (RUB)**: invoice + payment processing + subscription extension.
- **Automated Expiration Notifications**: scheduled reminders 60 minutes before expiry.
- **FSM Support (Support Desk)**: user → admin ticket flow with reply button.
- **Global Log Masking (Sensitive Data Sanitizer)**: tokens/cookies/passwords are masked as `[MASKED]` across the whole logging pipeline.
- **Strict `.gitignore` Hygiene**: protects `.env`, databases, venvs, caches, logs, and cert/key material from accidental commits.

---

## Architecture (Clean-ish, SoC-first)

The project is structured to keep responsibilities separated:

```text
.
├── app/
│   ├── config.py              # env config (dotenv) + constants
│   ├── db.py                  # SQLite helpers (users table)
│   ├── vless.py               # VLESS link builder
│   ├── panel_client.py        # aiohttp client: login, cookies, CSRF, requests
│   ├── logging_sanitizer.py   # global logging filter (mask secrets)
│   ├── services/
│   │   ├── vpn.py             # create/renew/rotate logic (business rules)
│   │   └── notifications.py   # scheduled expiry notifications
│   └── bot/
│       ├── dispatcher.py      # aiogram Dispatcher + handler registration
│       ├── keyboards.py       # reply keyboards
│       └── handlers/          # message/callback handlers grouped by domain
│           ├── common.py
│           ├── profile.py
│           ├── payments.py
│           ├── support.py
│           └── admin.py
├── main.py                    # entrypoint: logging + db + scheduler + polling
├── .env.example               # environment template
└── .gitignore
```

**Handlers** define Telegram UX. **Services** hold VPN/payment logic. **Core** modules (`panel_client`, `db`, `config`) provide infrastructure.

---

## Security

### Sensitive Data Masking (Global)

This project ships with a global logging filter (`app/logging_sanitizer.py`) that masks:

- Telegram bot tokens (e.g. `123456:ABC...`)
- `Cookie` and `Set-Cookie` headers
- JSON fields named `password`, `token`, `key`
- Any values loaded from `app.config` (env-derived secrets)

Everything is replaced with **`[MASKED]`** — no partial leaks.

### Git Hygiene

`.gitignore` is strict by default: it excludes `.env`, `*.db*`, venvs, caches, logs, and TLS keys/certs.

---

## Installation & Setup

### Requirements

- **Python 3.10+** (project runs on 3.12 as well)
- A reachable **3x-ui panel** (often on a custom port like `43256`)
- A configured **VLESS inbound** (REALITY or TLS), plus `INBOUND_ID`

### Clone

```bash
git clone <your-repo-url>
cd metron_vpn
```

### Virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -U pip
```

### Install dependencies

This project uses:

- `aiogram` (3.x)
- `aiohttp`
- `python-dotenv`
- `APScheduler`
- `urllib3`

Install them:

```bash
pip install aiogram aiohttp python-dotenv apscheduler urllib3
```

### Configure `.env`

Copy the template and fill it:

```bash
cp .env.example .env
```

`.env.example` includes all required variables:

- `BOT_TOKEN`, `ADMIN_ID`
- `PANEL_URL`, `PANEL_USER`, `PANEL_PASS`
- `SERVER_IP`, `INBOUND_ID`
- `PAY_TOKEN`
- `VLESS_*` (REALITY/TLS parameters)

---

## Usage

### Run the bot

```bash
python3 main.py
```

### Bot commands & flows

- **/start**: show the main menu
- **🚀 Подключить VPN**: create a trial key (24h) if user has no active key
- **👤 Мой профиль**: view subscription status and key
- **🔄 Обновить ключ**: rotate UUID (invalidate old key, issue new one)
- **💳 Продлить**: pay and extend subscription (30 days)
- **🆘 Поддержка**: FSM ticket to admin with inline “reply” action

---

## Tech Stack

- **Python 3.10+**
- **aiogram 3.x** (Telegram Bot API framework)
- **aiohttp** (async HTTP client)
- **SQLite** (local persistence)
- **APScheduler** (async scheduled jobs)

---

## Development

### Add a new handler

1. Create a file under `app/bot/handlers/` (e.g. `foo.py`).
2. Import `dp` from `app.bot.dispatcher` and register handlers with decorators.
3. Ensure it’s imported in `app/bot/dispatcher.py` so decorators execute.

### Add a new service

1. Put business logic in `app/services/`.
2. Keep Telegram-specific code inside handlers; services should be testable without Telegram context.

### Logging

Sensitive-data masking is enabled globally via:

- `main.py` (after `logging.basicConfig(...)`)
- `app/bot/dispatcher.py` (safety net)

If you add new logs, you can log freely — secrets will be masked.

---

## Disclaimer

This project is provided for educational and operational automation purposes. You are responsible for your own infrastructure, compliance, and lawful use.

---

## METRON_VPN — Продвинутый Telegram-бот менеджер VLESS/V2Ray

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)


High Performance & Maximum Privacy — без лишних движений.

METRON_VPN — это Telegram-бот, который автоматизирует выдачу VLESS-доступа через панель 3x-ui, продления/оплаты, ротацию ключей при компрометации и заранее предупреждает пользователей о скором окончании подписки. Плюс — **глобальная маскировка секретов в логах**.

---

## Key Features (Фишки)

- **Интеграция с 3x-ui (API)**: создание/продление/удаление клиентов через API панели.
- **Генерация VLESS/REALITY ссылок**: готовые к импорту ключи для пользователя.
- **Telegram Payments (RUB)**: выставление инвойса, обработка оплаты, продление подписки.
- **Авто-уведомления**: напоминание за 60 минут до окончания доступа.
- **FSM-поддержка**: тикет “пользователь → админ” + кнопка “ответить”.
- **Глобальная маскировка логов**: токены/куки/пароли автоматически заменяются на `[MASKED]`.
- **Жёсткий `.gitignore`**: защищает `.env`, базы `*.db*`, venv, кэши, логи и ключи/сертификаты от случайного коммита.

---

## Архитектура (Clean-ish, разделение ответственности)

Проект разделён на:

- **handlers**: Telegram UX (сообщения/кнопки/коллбеки)
- **services**: бизнес-логика VPN/оплаты/ротации
- **core**: конфиг, база, клиент панели, лог-санитайзер

Структура папок:

```text
app/
  config.py
  db.py
  vless.py
  panel_client.py
  logging_sanitizer.py
  services/
  bot/
    dispatcher.py
    keyboards.py
    handlers/
main.py
```

---

## Безопасность

### Маскировка чувствительных данных в логах

Встроен глобальный фильтр логирования (`app/logging_sanitizer.py`), который маскирует:

- Telegram токены
- `Cookie` / `Set-Cookie`
- поля `password` / `token` / `key` в JSON/строках
- любые значения, загруженные из `app.config` (секреты из `.env`)

Всегда заменяется строго на **`[MASKED]`**.

### Гигиена Git

`.gitignore` по умолчанию строгий: `.env`, `*.db*`, кэши, логи, venv, сертификаты/ключи — всё исключено.

---

## Установка и настройка

### Требования

- **Python 3.10+**
- Доступная **панель 3x-ui** (часто на кастомном порту, например `43256`)
- Настроенный **VLESS inbound** и корректный `INBOUND_ID`

### Установка

```bash
git clone <your-repo-url>
cd metron_vpn
python3 -m venv venv
source venv/bin/activate
python -m pip install -U pip
pip install aiogram aiohttp python-dotenv apscheduler urllib3
```

### Настройка `.env`

```bash
cp .env.example .env
```

Далее заполни переменные в `.env` (см. комментарии в `.env.example`):

- `BOT_TOKEN`, `ADMIN_ID`
- `PANEL_URL`, `PANEL_USER`, `PANEL_PASS`
- `SERVER_IP`, `INBOUND_ID`
- `PAY_TOKEN`
- `VLESS_*`

---

## Использование

Запуск:

```bash
python3 main.py
```

Дальше управляй ботом через кнопки меню: выдача ключа, профиль, обновление ключа, оплата и поддержка.

---

## Tech Stack (Стек)

- **aiogram 3.x**
- **aiohttp**
- **SQLite**
- **APScheduler**

---

## Разработка

### Как добавить новый handler

1. Создай файл в `app/bot/handlers/`.
2. Импортируй `dp` из `app.bot.dispatcher` и повесь декораторы.
3. Добавь импорт файла в `app/bot/dispatcher.py`, чтобы регистрации выполнились.

### Как добавить сервис

1. Пиши бизнес-логику в `app/services/`.
2. Не смешивай Telegram-слой и бизнес-слой — так проще тестировать и поддерживать.

---

## Дисклеймер

Проект предоставляется “как есть”. Ты несёшь ответственность за инфраструктуру, безопасность сервера и правомерность использования.

