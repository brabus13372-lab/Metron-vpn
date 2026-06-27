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

> **Telegram-бот + FastAPI WebApp** для автоматизации выдачи VLESS-доступа через панель [3x-ui](https://github.com/MHSanaei/3x-ui).  
> Управление устройствами, ежедневный биллинг, ротация ключей, оплата через YooKassa.

🇬🇧 [English version](README.md)

---

## Содержание

- [Архитектура](#архитектура)
- [Стек технологий](#стек-технологий)
- [Требования](#требования)
- [Установка](#установка)
- [Деплой (prod)](#деплой-prod)
- [Переменные окружения](#переменные-окружения)
- [API эндпоинты](#api-эндпоинты)
- [Биллинг](#биллинг)
- [Отказоустойчивость](#отказоустойчивость)
- [Безопасность](#безопасность)
- [Скрипты обслуживания](#скрипты-обслуживания)
- [Разработка](#разработка)

---

## Архитектура

```text
Metron-vpn/
│
├── main.py                          # Entrypoint: uvicorn + aiogram long-polling
│
├── app/
│   ├── core/
│   │   ├── panel_client.py          # aiohttp-клиент для 3x-ui REST API
│   │   ├── telegram_auth.py         # HMAC-SHA256 валидация Telegram initData
│   │   ├── logging_sanitizer.py     # Глобальный фильтр логов (маскирует токены/куки)
│   │   └── vless.py                 # Сборка VLESS-ссылки
│   ├── db/
│   │   ├── core.py                  # asyncpg pool + проверка схемы
│   │   ├── users.py                 # запросы пользователей
│   │   ├── devices.py               # запросы устройств
│   │   ├── billing.py               # запросы баланса и биллинга
│   │   └── payments.py              # идемпотентные запросы платежей
│   ├── schemas/
│   │   └── user.py                  # Pydantic-модели запросов / ответов
│   ├── deps.py                      # FastAPI зависимости: AuthUserId (проверка initData)
│   ├── services/
│   │   ├── billing.py               # BillingEngine: ежедневное списание + TRIAL-цикл
│   │   ├── payment_sweeper.py       # Повтор apply для платежей в статусе RECEIVED
│   │   ├── notifications.py         # APScheduler: напоминания по подписке
│   │   ├── reconcile.py             # Воркер сверки БД ↔ панель
│   │   └── vpn.py                   # Бизнес-логика панели и устройств
│   └── bot/
│       ├── bot.py                   # Экземпляр aiogram Bot
│       ├── dispatcher.py            # Dispatcher + регистрация хэндлеров
│       ├── keyboards.py             # Reply-клавиатуры
│       └── handlers/
│           ├── common.py            # /start, главное меню
│           ├── profile.py           # Профиль, устройства, ключи
│           ├── payments.py          # YooKassa: invoice → pre_checkout → успех
│           ├── support.py           # FSM тикет-система (пользователь → админ)
│           └── admin.py             # Админ-команды: рассылка, статистика
│
├── webapp/                          # Telegram WebApp (статика, отдаётся FastAPI)
│   ├── index.html                   # Redirect-entry с сохранением query string
│   ├── pages/
│   │   ├── profile.html             # Основная страница профиля/устройств/биллинга
│   │   ├── support.html             # Форма поддержки и история тикетов
│   │   └── protocols.html           # Статическая страница с протоколами
│   ├── assets/
│   │   ├── css/
│   │   │   ├── base.css
│   │   │   ├── components.css
│   │   │   ├── animations.css
│   │   │   └── pages/               # Вынесенные page-specific стили
│   │   └── js/
│   │       ├── api.js               # Общий API-клиент WebApp
│   │       └── pages/               # Вынесенные page-specific скрипты
│   └── legacy/
│       └── assets/js/               # Архив устаревших WebApp-модулей
│
├── scripts/
│   ├── maintenance/
│   │   └── cleanup_orphan_keys.py   # Каноническая очистка orphan account-key
│   └── migrations/
│       └── migrate_db.py            # Каноническая миграция SQLite → PostgreSQL
├── cleanup_orphan_keys.py           # Backward-compatible wrapper
├── migrate_db.py                    # Backward-compatible wrapper
├── migrations/                      # Alembic env + versions
├── alembic.ini
├── requirements.txt
├── .env.example
└── .gitignore
```

**Потоки данных:**
- `Telegram → aiogram handlers → services → db / panel_client`
- `WebApp → FastAPI (api.py) [AuthUserId dep] → db / panel_client → 3x-ui`

**Стабильные продовые entrypoints:**
- `main.py` — точка входа сервиса для `systemd`
- `app/` — Python import root
- `webapp/` — static root, смонтированный FastAPI
- `migrations/` + `alembic.ini` — runtime-root для Alembic
- Root-wrapper'ы `cleanup_orphan_keys.py` и `migrate_db.py` остаются поддерживаемыми для операторского удобства

---

## Стек технологий

| Слой | Библиотека | Версия |
|---|---|---|
| Bot framework | aiogram | 3.20 |
| Web API | FastAPI + uvicorn | 0.115+ |
| HTTP (панель) | aiohttp | latest |
| HTTP (скрипты) | httpx | 0.27+ |
| Database | PostgreSQL / asyncpg | latest |
| Scheduler | APScheduler | 3.11 |
| Валидация | Pydantic v2 | latest |
| Config | python-dotenv | latest |
| Payments | YooKassa (Telegram Payments) | — |
| Forms/Files | python-multipart | latest |
| Cache | cachetools | latest |
| Timezone | tzdata | latest |

> `urllib3` удалён — синхронная библиотека, нигде не использовалась.

---

## Требования

### Панель

Бот работает с **[3x-ui](https://github.com/MHSanaei/3x-ui)** — панелью с REST API для управления Xray.

- Работающий инстанс 3x-ui, доступный с хоста бота
- Настроенный **VLESS inbound** (REALITY или TLS) с известным `INBOUND_ID`
- Учётные данные панели (`PANEL_USER` / `PANEL_PASS`)

> Инструкция по установке: [3x-ui Wiki](https://github.com/MHSanaei/3x-ui/wiki)

### Программное обеспечение

- Python **3.10+**
- PostgreSQL (доступен с хоста бота)

---

## Установка

```bash
git clone https://github.com/brabus13372-lab/Metron-vpn.git
cd Metron-vpn

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -U pip
pip install -r requirements.txt

cp .env.example .env
# Заполни .env — см. раздел «Переменные окружения»

python3 main.py
```

---

## Деплой (prod)

### systemd

Сервис слушает **`:8081`** (FastAPI + статика WebApp) и параллельно поднимает aiogram polling.

```ini
# /etc/systemd/system/metron2.service
[Service]
WorkingDirectory=/path/to/metron_vpn_twoversion
ExecStart=/path/to/metron_vpn_twoversion/.venv/bin/python3 main.py
Restart=always
```

`.env` загружается через `python-dotenv` в `app/config.py` (отдельный `EnvironmentFile` в unit не обязателен).

```bash
systemctl daemon-reload
systemctl enable --now metron2.service
journalctl -u metron2.service -f
```

### База данных

```bash
set -a && source .env && set +a
.venv/bin/alembic upgrade head          # применить схему
.venv/bin/alembic downgrade base        # полный сброс (destructive!)
.venv/bin/alembic upgrade head
```

### WebApp: nginx + HTTPS

Telegram Mini App **требует HTTPS**. Uvicorn отдаёт API и статику на `127.0.0.1:8081`; снаружи — reverse proxy.

**Вариант A — отдельный поддомен** (рекомендуется):

```nginx
server {
    listen 443 ssl;
    server_name pnv.example.com;
    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

`.env` и @BotFather Menu Button:

```env
WEBAPP_URL=https://pnv.example.com/pages/profile.html
```

**Вариант B — подпуть на существующем домене** (fallback, если DNS поддомена нет):

```nginx
# edge.example.com — статика и API Metron под /pnv/
location /pnv/ {
    proxy_pass http://127.0.0.1:8081/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
# API без префикса /pnv/ в fetch — отдельный location обязателен
location /api/ {
    proxy_pass http://127.0.0.1:8081/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

```env
WEBAPP_URL=https://edge.example.com/pnv/pages/profile.html
```

Фронт (`webapp/assets/js/api.js`) автоматически добавляет префикс `/pnv` к API-запросам, если WebApp открыт из `/pnv/…`.

> ⚠️ **DNS:** если домен из `WEBAPP_URL` не резолвится (`ERR_NAME_NOT_RESOLVED`), WebApp не откроется — это не ошибка бота. Проверка: `dig +short your-domain @8.8.8.8`.

> ⚠️ **502 на `/api/*`:** при прокси через подпуть убедись, что `/api/` тоже проксируется на `:8081`, а не на другой upstream.

### VLESS / xhttp

Параметры ссылки (`VLESS_TYPE`, `VLESS_XHTTP_*`, `VLESS_PBK`, `VLESS_SID`, …) **должны совпадать с inbound в 3x-ui**.  
`INBOUND_ID` в `.env` — ID inbound в панели. Сверка: скопируй тестовую ссылку из 3x-ui и сравни query-параметры с тем, что генерит бот.

---

## Переменные окружения

Все переменные описаны в `.env.example`. Обязательные:

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Telegram bot token от @BotFather |
| `ADMIN_ID` | Telegram user ID администратора (число) |
| `PANEL_URL` | Base URL панели 3x-ui (без trailing `/`) |
| `PANEL_USER` | Логин в панели |
| `PANEL_PASS` | Пароль в панели |
| `INBOUND_ID` | ID inbound в панели (integer, обычно `1`) |
| `SERVER_IP` | Публичный IP сервера (вставляется в VLESS-ссылки) |
| `DATABASE_URL` | asyncpg DSN: `postgresql://user:pass@host/db` |
| `VLESS_PBK` | REALITY public key |
| `VLESS_TYPE` | Транспорт: `tcp` или `xhttp` |
| `VLESS_XHTTP_PATH` / `HOST` / `MODE` | Параметры XHTTP (см. `.env.example`) |
| `PAY_TOKEN` | YooKassa provider token (получить через @BotFather) |
| `WEBAPP_URL` | HTTPS URL Mini App, напр. `https://pnv.example.com/pages/profile.html` |
| `TRIAL_DAYS` | Длительность trial при `/start` (дней, default `1`) |
| `BOT_NAME` | Username бота без `@` (для deep-link topup/support в WebApp) |

> ⚠️ **Никогда не коммить `.env`.** Файл исключён в `.gitignore`.

---

## API эндпоинты

WebApp взаимодействует с ботом через REST API (`app/api.py`).

### Профиль

| Метод | URL | Описание |
|---|---|---|
| `GET` | `/api/user/{id}` | Профиль: баланс, статус, устройства, ключ |
| `GET` | `/api/user/{id}/billing` | Биллинг: баланс, стоимость/день, дней осталось |

### Устройства

| Метод | URL | Описание |
|---|---|---|
| `GET` | `/api/user/{id}/devices` | Список устройств |
| `POST` | `/api/user/{id}/devices` | Создать устройство (панель + DB) |
| `DELETE` | `/api/user/{id}/devices/{dev_id}` | Деактивировать устройство (soft) |
| `DELETE` | `/api/user/{id}/devices/{dev_id}/hard` | Удалить из панели и DB (hard) |
| `POST` | `/api/user/{id}/devices/{dev_id}/rotate` | Ротировать ключ устройства |

### Ключ аккаунта

| Метод | URL | Описание |
|---|---|---|
| `POST` | `/api/user/{id}/rotate-key` | Выдать / ротировать аккаунтный ключ |

> ⚠️ **Гард:** `/rotate-key` возвращает `409` если у пользователя уже есть активные устройства.  
> Аккаунтный ключ предназначен **только для первичной активации** (TRIAL, ещё нет устройств).  
> Все последующие ключи создаются через `POST /devices`.

### Поддержка

| Метод | URL | Описание |
|---|---|---|
| `POST` | `/api/user/{id}/support` | Создать тикет (текст + до 5 файлов ≤10 MB) |
| `GET` | `/api/user/{id}/support` | История тикетов (последние 20) |

### Системные

| Метод | URL | Описание |
|---|---|---|
| `GET` | `/health` | Healthcheck |
| `GET` | `/api/config` | Имя бота |

---

## Биллинг

`app/services/billing.py` — `BillingEngine` на APScheduler.

**Логика:**
1. Раз в сутки списывает `SUM(devices.monthly_cost) / 30` с баланса пользователя
2. При нулевом балансе — деактивирует все устройства, статус переходит в `EXPIRED`
3. TRIAL: по истечении срока — автоматический перевод на платную подписку или деактивация
4. **Автовосстановление при пополнении:** если статус пользователя был `EXPIRED`, `INACTIVE` или `NEW`, он атомарно переводится в `ACTIVE` в той же транзакции что и зачисление баланса — race condition исключён
5. Уведомления: APScheduler отправляет напоминания до истечения доступа (раннее предупреждение + критическое)

> ⚠️ **Критически важно:** биллинг считает **только записи в таблице `devices`**.  
> Аккаунтный ключ (`users.vless_link`) **не тарифицируется**.  
> Не создавай клиентов в панели в обход таблицы `devices` — это дыра в биллинге.

---

## Отказоустойчивость

Слой компенсации и graceful degradation (не two-phase commit — **reconcile** остаётся safety net).

### Платежи (`app/bot/handlers/payments.py`)

- `record_payment_idempotent` → `apply_payment_topup_idempotent` с **одним retry**
- После успешного apply юзер **всегда** получает подтверждение; activation/devices — best-effort
- Если apply упал после record — юзеру «оплата получена, баланс обновится», админу alert с `payment_id`
- Deep-link `/start topup|pay` из WebApp → экран пополнения
- **RECEIVED sweeper** — каждые 15 мин (`BillingEngine`) повторяет `apply_payment_topup_idempotent`
- **pre_checkout** — payload `user_id` должен совпадать с плательщиком

### Panel ↔ DB (add device)

- Panel OK → DB fail → `rollback_orphan_panel_client()` в bot и API `create_device`
- При неудачном rollback — подхватывает `run_reconcile_loop`

### API / Bot errors

- WebApp API: panel/internal errors → `_INTERNAL_ACTION_ERROR` (детали только в log + admin notify)
- `rotate_device_key` → `safe_rotate_device_key` (panel rollback при DB fail)
- Bot: global `@dp.errors()` handler; support FSM сбрасывается **после** успешного forward
- `/start` dedup: короткий ответ вместо молчаливого drop

### Тесты

```bash
pytest -q   # 74 regression tests (auth, payments, rollback, resilience, webapp API)
```

Основные группы:
- `tests/test_telegram_auth.py`, `test_deps_auth.py`, `test_http_endpoints.py` — auth / IDOR / HTTP guards
- `tests/test_bot_*.py`, `test_post_deploy_hardening.py` — бот, платежи, pre_checkout, sweeper
- `tests/test_api_internal_errors.py`, `test_add_device_rollback.py` — panel rollback, sanitized API errors
- `tests/test_vless_link.py` — сборка VLESS (tcp / xhttp + REALITY)
- `tests/test_user_scenarios.py` — billing, payments idempotency

---

## Безопасность

### Маскировка логов

`app/logging_sanitizer.py` — глобальный фильтр, заменяет `[MASKED]`:
- Telegram bot tokens
- `Cookie` / `Set-Cookie` заголовки
- JSON-поля: `password`, `token`, `key`
- Runtime-секреты из `app.config`

### Аутентификация Telegram WebApp (защита от IDOR)

Все эндпоинты `/api/user/{id}/*` проверяют заголовок `X-Telegram-Init-Data` через HMAC-SHA256 (`app/core/telegram_auth.py`).  
Извлечённый из `initData` `user_id` должен совпадать с `{id}` в пути запроса.  
Запросы без валидного `initData` или с несовпадающим `user_id` отклоняются с `403`.

- `app/core/telegram_auth.py` — ядро HMAC-SHA256 валидации
- `app/deps.py` — зависимость `AuthUserId`, применяемая на всех защищённых эндпоинтах
- `webapp/assets/js/api.js` — автоматически подставляет `X-Telegram-Init-Data` в каждый запрос

### SSL

> ⚠️ Запросы к 3x-ui выполняются с `ssl=False` (aiohttp) для самоподписанных сертификатов.  
> Если у панели валидный CA-сертификат — убери `ssl=False` в `app/core/panel_client.py`.

### Серверные гарды

| Эндпоинт | Код | Условие |
|---|---|---|
| `POST /rotate-key` | `409` | У пользователя уже есть записи в `devices` |
| `DELETE /devices/{id}` | `409` | Удаляется последнее активное устройство |
| `POST /devices` | `403` | Статус пользователя не `ACTIVE` / `TRIAL` |
| `POST /devices` | `409` | Достигнут лимит устройств (макс. 5 на пользователя) |
| `POST /support` | `429` | Rate limit: не чаще 1 тикета в 60 секунд |
| Все `/api/user/{id}/*` | `403` | `initData` невалиден или `user_id` не совпадает |

Валидация ввода:
- `device_name` — от 1 до 50 символов (Pydantic `Field`, проверяется до вызова панели)
- Сообщение тикета — от 5 до 1000 символов (DB `CHECK` + FastAPI `Form`)

> Все гарды продублированы на сервере — клиентская проверка в WebApp не является единственной защитой.

### Git hygiene

`.gitignore` исключает: `.env`, `*.db*`, venv, кэши, логи, TLS-ключи/сертификаты.

---

## Скрипты обслуживания

### `cleanup_orphan_keys.py`

**Одноразовый миграционный скрипт.** Удаляет «призрачные» аккаунтные клиенты в 3x-ui панели — те, что были созданы через `/rotate-key` у пользователей, которые уже перешли на `devices`.

**Когда запускать:** один раз, сразу после деплоя гарда в `/rotate-key`.

> ⚠️ Перед `--apply` — обязательно проверь список через `--dry-run`.

```bash
# Dry-run: показать что будет удалено (ничего не меняет).
# Backward-compatible root-wrapper:
export $(grep -v '^#' .env | xargs) && python cleanup_orphan_keys.py --dry-run

# Канонический путь скрипта:
export $(grep -v '^#' .env | xargs) && .venv/bin/python -m scripts.maintenance.cleanup_orphan_keys --dry-run

# Apply: применить очистку.
# Backward-compatible root-wrapper:
export $(grep -v '^#' .env | xargs) && python cleanup_orphan_keys.py --apply

# Канонический путь скрипта:
export $(grep -v '^#' .env | xargs) && .venv/bin/python -m scripts.maintenance.cleanup_orphan_keys --apply
```

Скрипт **идемпотентен** — повторный запуск безопасен. Если панель недоступна для какого-то UUID — БД не трогается, пользователь появится снова при следующем запуске.

### `migrate_db.py`

Одноразовая миграция данных SQLite → PostgreSQL (использовалась при переходе на prod-базу). Для новых установок не нужен.

Каноническая реализация: `.venv/bin/python -m scripts.migrations.migrate_db`

Backward-compatible wrapper: `python migrate_db.py`

---

## Разработка

### Тесты

В проекте есть **регрессионный набор security-тестов** на чувствительные места (auth/IDOR, серверные гарды, ключевые пользовательские сценарии).

```bash
# Установить зависимости (включая pytest)
source .venv/bin/activate
pip install -r requirements.txt

# Запустить тесты
pytest -q
```

Основные тесты:
- `tests/test_telegram_auth.py` — проверка Telegram WebApp `initData` (HMAC, срок действия)
- `tests/test_deps_auth.py` — FastAPI зависимость `AuthUserId` (401/403)
- `tests/test_http_endpoints.py` — e2e HTTP проверки через ASGI client (401/403/409/429)
- `tests/test_user_scenarios.py` — сценарии пользователей (billing/status/идемпотентность платежей)
- `tests/test_bot_resilience.py`, `test_post_deploy_hardening.py` — отказоустойчивость и post-deploy hardening

### Добавить хэндлер

1. Создай файл в `app/bot/handlers/`
2. Используй `dp` из `app.bot.dispatcher`, регистрируй декораторами
3. Импортируй файл в `app/bot/dispatcher.py`

### Добавить сервис

1. Бизнес-логику помести в `app/services/`
2. Telegram-специфичный код держи в хэндлерах — сервисы тестируемы независимо

### Добавить API эндпоинт

1. Добавь эндпоинт в `app/api.py`
2. Добавь Pydantic-схемы в `app/schemas/`
3. SQL-запросы — только в `app/db/` (никакого inline SQL в `api.py`)

### Логирование

Логируй свободно — `logging_sanitizer.py` автоматически замаскирует секреты.

---

## Disclaimer

Предоставляется «как есть» для образовательных и операционных целей автоматизации. Ответственность за инфраструктуру, безопасность сервера и законность использования лежит на пользователе.
