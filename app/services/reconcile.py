"""
Reconcile worker — синхронизация состояния девайсов в БД и 3x-ui панели.

Логика одного прохода:

1. Берём все девайсы из БД (is_active=True).
2. Берём все клиенты из панели одним запросом в get_inbound.
3. Для каждого девайса:
   - если клиент отсутствует в панели → создаём заново
   - если клиент есть но выключен, а должен быть включён → включаем
   - если клиент есть и включён, а должен быть выключен (balance=0) → выключаем
4. Орфаны в панели (есть в панели, нет в БД) → WARNING в лог.

Также содержит:
- safe_rotate_device_key(): корректная ротация UUID девайса с откатом.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from app.config import INBOUND_ID
from app.core.panel_client import (
    get_inbound,
    parse_inbound_settings,
    update_client_fields,
    panel_request,
)
from app.core.vless import build_vless_link
from app.db import get_all_active_devices, get_user_data_dict, update_device_link
from app.services.vpn import (
    _build_device_email,
    _build_client_settings,
    _build_form_data,
    _panel_expiry_ts_for_dynamic_access,
    _generate_sub_id,
)

logger = logging.getLogger(__name__)

# Интервал между проходами в секундах
RECONCILE_INTERVAL_SEC = 120


# ---------------------------------------------------------------------------
# Сбор состояния панели
# ---------------------------------------------------------------------------

async def _fetch_panel_clients() -> dict[str, dict[str, Any]]:
    """
    Возвращает {client_uuid: client_dict} для всех клиентов из inbound.
    При ошибке возвращает пустой словарь (reconcile пропустит цикл).
    """
    inbound = await get_inbound(INBOUND_ID)
    if not inbound:
        logger.error("reconcile.fetch_panel.fail inbound_id=%s", INBOUND_ID)
        return {}

    settings = parse_inbound_settings(inbound)
    clients: list[dict] = settings.get("clients", [])
    return {c["id"]: c for c in clients if c.get("id")}


# ---------------------------------------------------------------------------
# Репарация: создание отсутствующего клиента
# ---------------------------------------------------------------------------

async def _recreate_missing_client(
    dev: dict[str, Any],
    user_id: int,
    username: str,
) -> None:
    """
    Клиент есть в БД, но отсутствует в панели.
    Создаём новый клиент с новым UUID и обновляем БД.
    """
    old_uuid = dev["client_uuid"]
    device_name = dev["device_name"]
    new_uuid = str(uuid.uuid4())
    email = _build_device_email(user_id, device_name, new_uuid)

    client_settings = _build_client_settings(
        client_uuid=new_uuid,
        user_id=user_id,
        email=email,
        expiry_ts=_panel_expiry_ts_for_dynamic_access(),
        enabled=True,
        sub_id=_generate_sub_id(),
        comment=f"Device: {device_name} [reconciled]",
    )

    res = await panel_request(
        "POST",
        "/panel/api/inbounds/addClient",
        data=_build_form_data(client_settings),
    )

    if not res.get("success"):
        logger.error(
            "reconcile.recreate.fail user_id=%s device=%s old_uuid=%s msg=%s",
            user_id, device_name, old_uuid, res.get("msg"),
        )
        return

    # Панель приняла новый UUID — обновляем БД
    new_link = build_vless_link(new_uuid, username)
    await update_device_link(dev["id"], new_uuid, new_link)

    logger.warning(
        "reconcile.recreate.ok user_id=%s device=%s old_uuid=%s new_uuid=%s",
        user_id, device_name, old_uuid, new_uuid,
    )


# ---------------------------------------------------------------------------
# Один проход reconcile
# ---------------------------------------------------------------------------

async def reconcile_once() -> None:
    """
    Запускает одинцикл сверки БД ↔ панель.
    """
    logger.info("reconcile.start")

    # 1. Собираем данные
    panel_clients = await _fetch_panel_clients()
    if not panel_clients:
        # Панель недоступна — пропускаем цикл, не портим БД
        logger.warning("reconcile.skip reason=panel_unavailable")
        return

    db_devices = await get_all_active_devices()
    db_uuids: set[str] = {d["client_uuid"] for d in db_devices}

    # 2. Орфаны в панели
    orphan_uuids = set(panel_clients.keys()) - db_uuids
    for orphan_uuid in orphan_uuids:
        c = panel_clients[orphan_uuid]
        logger.warning(
            "reconcile.orphan uuid=%s email=%s enabled=%s",
            orphan_uuid, c.get("email"), c.get("enable"),
        )

    # 3. Проверяем каждый активный девайс из БД
    fixed = 0
    errors = 0

    for dev in db_devices:
        client_uuid = dev["client_uuid"]
        user_id = dev["user_id"]
        device_name = dev["device_name"]

        # Получаем данные пользователя для определения желаемого состояния
        user = await get_user_data_dict(user_id)
        if not user:
            logger.warning(
                "reconcile.skip_device user_id=%s device=%s reason=user_not_found",
                user_id, device_name,
            )
            continue

        username = user.get("username") or f"user_{user_id}"
        user_balance: int = user.get("balance", 0)  # cents
        user_status: str = user.get("status", "INACTIVE")

        # Должен ли клиент быть включён:
        # - статус ACTIVE и баланс > 0
        # - девайс не выключен вручную (user_request)
        should_be_enabled = (
            user_status == "ACTIVE"
            and user_balance > 0
            and dev.get("disabled_reason") != "user_request"
        )

        panel_client = panel_clients.get(client_uuid)

        try:
            if panel_client is None:
                # Клиент есть в БД но отсутствует в панели
                logger.warning(
                    "reconcile.missing user_id=%s device=%s uuid=%s should_enabled=%s",
                    user_id, device_name, client_uuid, should_be_enabled,
                )
                if should_be_enabled:
                    await _recreate_missing_client(dev, user_id, username)
                    fixed += 1

            else:
                panel_enabled: bool = panel_client.get("enable", False)

                if should_be_enabled and not panel_enabled:
                    # Должен быть включён, но выключен
                    ok, msg = await update_client_fields(
                        client_uuid, {"enable": True}, inbound_id=INBOUND_ID
                    )
                    if ok:
                        logger.warning(
                            "reconcile.fixed_enable user_id=%s device=%s uuid=%s",
                            user_id, device_name, client_uuid,
                        )
                        fixed += 1
                    else:
                        logger.error(
                            "reconcile.fix_enable.fail user_id=%s device=%s uuid=%s msg=%s",
                            user_id, device_name, client_uuid, msg,
                        )
                        errors += 1

                elif not should_be_enabled and panel_enabled:
                    # Должен быть выключен, но включён
                    ok, msg = await update_client_fields(
                        client_uuid, {"enable": False}, inbound_id=INBOUND_ID
                    )
                    if ok:
                        logger.warning(
                            "reconcile.fixed_disable user_id=%s device=%s uuid=%s",
                            user_id, device_name, client_uuid,
                        )
                        fixed += 1
                    else:
                        logger.error(
                            "reconcile.fix_disable.fail user_id=%s device=%s uuid=%s msg=%s",
                            user_id, device_name, client_uuid, msg,
                        )
                        errors += 1

        except Exception:
            logger.exception(
                "reconcile.exception user_id=%s device=%s uuid=%s",
                user_id, device_name, client_uuid,
            )
            errors += 1

    logger.info(
        "reconcile.done total_db=%s panel=%s orphans=%s fixed=%s errors=%s",
        len(db_devices), len(panel_clients), len(orphan_uuids), fixed, errors,
    )


# ---------------------------------------------------------------------------
# Бесконечный цикл
# ---------------------------------------------------------------------------

async def run_reconcile_loop() -> None:
    """
    Бесконечный цикл для запуска через asyncio.create_task().
    Первый проход запускается через RECONCILE_INTERVAL_SEC после старта
    (чтобы не гнать панель при инициализации).
    """
    logger.info(
        "reconcile_loop.start interval=%ss", RECONCILE_INTERVAL_SEC
    )
    while True:
        await asyncio.sleep(RECONCILE_INTERVAL_SEC)
        try:
            await reconcile_once()
        except Exception:
            # Никогда не даём воркеру умереть — логируем и продолжаем
            logger.exception("reconcile_loop.unhandled_exception")


# ---------------------------------------------------------------------------
# Безопасная ротация UUID девайса
# ---------------------------------------------------------------------------

async def safe_rotate_device_key(
    device_id: int,
    old_uuid: str,
    user_id: int,
    username: str,
    device_name: str,
) -> tuple[str | None, str | None, str | None]:
    """
    Атомарная ротация UUID девайса:
      1. Обновляем UUID в панели (old -> new)
      2. Обновляем БД атомарно
      3. Если БД не успела — откатываем UUID обратно в панели

    Возвращает (new_link, new_uuid, error_message).
    """
    new_uuid = str(uuid.uuid4())

    # Шаг 1: обновляем в панели
    ok, msg = await update_client_fields(
        old_uuid,
        {"id": new_uuid},
        inbound_id=INBOUND_ID,
    )
    if not ok:
        logger.error(
            "safe_rotate.panel_fail device_id=%s old_uuid=%s msg=%s",
            device_id, old_uuid, msg,
        )
        return None, None, f"Panel update failed: {msg}"

    new_link = build_vless_link(new_uuid, username)

    # Шаг 2: обновляем БД
    try:
        await update_device_link(device_id, new_uuid, new_link)
    except Exception as exc:
        # БД не обновилась — откатываем UUID в панели
        logger.error(
            "safe_rotate.db_fail device_id=%s new_uuid=%s exc=%s — rolling back panel",
            device_id, new_uuid, exc,
        )
        rollback_ok, rollback_msg = await update_client_fields(
            new_uuid,
            {"id": old_uuid},
            inbound_id=INBOUND_ID,
        )
        if not rollback_ok:
            # Худший случай: панель поменяла UUID, БД нет, откат сфейлил
            # reconcile поправит на следующем цикле
            logger.critical(
                "safe_rotate.rollback_fail device_id=%s new_uuid=%s rollback_msg=%s "
                "INCONSISTENT STATE — reconcile will fix on next cycle",
                device_id, new_uuid, rollback_msg,
            )
        return None, None, f"DB write failed: {exc}"

    logger.info(
        "safe_rotate.ok device_id=%s user_id=%s old_uuid=%s new_uuid=%s",
        device_id, user_id, old_uuid, new_uuid,
    )
    return new_link, new_uuid, None
