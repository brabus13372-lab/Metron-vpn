# Metron VPN — Telegram VLESS Bot + WebApp

[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![aiogram](https://img.shields.io/badge/aiogram-3.20-009ddc?logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
[![asyncpg](https://img.shields.io/badge/PostgreSQL-asyncpg-336791?logo=postgresql&logoColor=white)](https://magicstack.github.io/asyncpg/)
[![APScheduler](https://img.shields.io/badge/APScheduler-3.11-orange)](https://apscheduler.readthedocs.io/)
[![3x-ui](https://img.shields.io/badge/panel-3x--ui-red?logo=github)](https://github.com/MHSanaei/3x-ui)
[![YooKassa](https://img.shields.io/badge/payments-YooKassa-8b5cf6)](https://yookassa.ru/)
[![httpx](https://img.shields.io/badge/HTTP-httpx-brightgreen)](https://www.python-httpx.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> **Telegram-бот + FastAPI WebApp** для автоматизации выдачи VLESS-доступа через панель [3x-ui](https://github.com/MHSanaei/3x-ui).  
> Управление устройствами, ежедневный биллинг, ротация ключей, оплата через YooKassa.

---

## Содержание

- [Архитектура](#архитектура)
- [Стек технологий](#стек-технологий)
- [Требования](#требования)
- [Установка](#установка)
- [Переменные окружения](#переменные-окружения)
- [API эндпоинты](#api-эндпоинты)
- [Биллинг](#биллинг)
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
│   ├── config.py                    # dotenv → константы (BOT_TOKEN, PANEL_URL, …)
│   ├── api.py                       # FastAPI: все REST-эндпоинты WebApp
│   ├── db.py                        # asyncpg: все SQL-запросы
│   ├── schemas.py                   # Pydantic v2 модели запросов / ответов
│   ├── vless.py                     # Сборка VLESS-ссылки из компонентов
│   ├── logging_sanitizer.py         # Глобальный фильтр логов (маскирует токены/куки)
│   │
│   ├── core/
│   │   └── panel_client.py          # httpx-клиент для 3x-ui REST API (login, CRUD)
│   │
│   ├── services/
│   │   ├── billing.py               # BillingEngine: ежедневное списание + TRIAL-цикл
│   │   ├── vpn.py                   # Бизнес-логика: rotate_user_key, add_device_to_panel
│   │   └── notifications.py         # APScheduler: напоминания об истечении подписки
│   │
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
│   └── index.html                   # SPA: профиль, устройства, биллинг, поддержка
│
├── migrate_db.py                    # Одноразовая миграция SQLite → PostgreSQL
├── cleanup_orphan_keys.py           # ⚠️  Сервисный скрипт: чистка orphan-клиентов
├── requirements.txt
├── .env.example
└── .gitignore
```

**Потоки данных:**
- `Telegram → aiogram handlers → services → db / panel_client`
- `WebApp → FastAPI (api.py) → db / panel_client → 3x-ui`

---

## Стек технологий

| Слой | Библиотека | Версия |
|---|---|---|
| Bot framework | aiogram | 3.20 |
| Web API | FastAPI + uvicorn | 0.115+ |
| HTTP (панель) | httpx | 0.27+ |
| Database | PostgreSQL / asyncpg | latest |
| Scheduler | APScheduler | 3.11 |
| Валидация | Pydantic v2 | latest |
| Config | python-dotenv | latest |
| Payments | YooKassa (Telegram Payments) | — |
| Forms/Files | python-multipart | latest |
| Cache | cachetools | latest |
| Timezone | tzdata | latest |

> **Примечание:** `aiohttp` и `urllib3` удалены — весь HTTP теперь через `httpx` (async-first, единый клиент).

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
| `PAY_TOKEN` | YooKassa provider token (получить через @BotFather) |

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
2. При нулевом балансе — деактивирует подписку (`status = SUSPENDED`)
3. TRIAL: по истечении срока — автоматический перевод на платную подписку или деактивация
4. Уведомления: APScheduler отправляет напоминания до истечения доступа

> ⚠️ **Критически важно:** биллинг считает **только записи в таблице `devices`**.  
> Аккаунтный ключ (`users.vless_link`) **не тарифицируется**.  
> Не создавай клиентов в панели в обход таблицы `devices` — это дыра в биллинге.

---

## Безопасность

### Маскировка логов

`app/logging_sanitizer.py` — глобальный фильтр, заменяет `[MASKED]`:
- Telegram bot tokens
- `Cookie` / `Set-Cookie` заголовки
- JSON-поля: `password`, `token`, `key`
- Runtime-секреты из `app.config`

### SSL

> ⚠️ Запросы к 3x-ui выполняются с `verify=False` (httpx) для самоподписанных сертификатов.  
> Если у панели валидный CA-сертификат — убери `verify=False` в `app/core/panel_client.py`.

### Серверные гарды

| Эндпоинт | Код | Условие |
|---|---|---|
| `POST /rotate-key` | `409` | У пользователя есть активные devices |
| `DELETE /devices/{id}` | `409` | Удаляется последнее активное устройство |
| `POST /devices` | `403` | Статус пользователя не `ACTIVE` / `TRIAL` |

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
# Dry-run: показать что будет удалено (ничего не меняет)
export $(grep -v '^#' .env | xargs) && python cleanup_orphan_keys.py --dry-run

# Apply: применить очистку
export $(grep -v '^#' .env | xargs) && python cleanup_orphan_keys.py --apply
```

Скрипт **идемпотентен** — повторный запуск безопасен. Если панель недоступна для какого-то UUID — БД не трогается, пользователь появится снова при следующем запуске.

### `migrate_db.py`

Одноразовая миграция данных SQLite → PostgreSQL (использовалась при переходе на prod-базу). Для новых установок не нужен.

---

## Разработка

### Добавить хэндлер

1. Создай файл в `app/bot/handlers/`
2. Используй `dp` из `app.bot.dispatcher`, регистрируй декораторами
3. Импортируй файл в `app/bot/dispatcher.py`

### Добавить сервис

1. Бизнес-логику помести в `app/services/`
2. Telegram-специфичный код держи в хэндлерах — сервисы тестируемы независимо

### Добавить API эндпоинт

1. Добавь эндпоинт в `app/api.py`
2. Добавь Pydantic-схемы в `app/schemas.py`
3. SQL-запросы — только в `app/db.py` (никакого inline SQL в `api.py`)

### Логирование

Логируй свободно — `logging_sanitizer.py` автоматически замаскирует секреты.

---

## Disclaimer

Предоставляется «как есть» для образовательных и операционных целей автоматизации. Ответственность за инфраструктуру, безопасность сервера и законность использования лежит на пользователе.
