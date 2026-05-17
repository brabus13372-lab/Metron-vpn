import json
import logging
import random
import string
import uuid
import asyncio

from datetime import datetime, timedelta

from app.config import INBOUND_ID
from app.panel_client import panel_request
from app.vless import build_vless_link


logger = logging.getLogger(__name__)


def _generate_sub_id(length: int = 16) -> str:
    """Генерирует случайный subId для клиента."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _build_main_client_email(user_id: int, client_uuid: str) -> str:
    """Формирует email основного клиента."""
    return f"metron_{user_id}_{client_uuid[:8]}"


def _build_device_email(user_id: int, device_name: str, client_uuid: str) -> str:
    """Формирует email для устройства."""
    safe_device = (device_name or "device").strip().replace(" ", "_")[:20]
    return f"TG_{user_id}_{safe_device}_{client_uuid[:8]}"


def _calc_expiry_ts(days: int) -> int:
    """Вычисляет timestamp истечения в миллисекундах."""
    if days <= 0:
        return 0
    return int((datetime.now() + timedelta(days=days)).timestamp() * 1000)


def _build_client_settings(
    *,
    client_uuid: str,
    user_id: int,
    email: str,
    expiry_ts: int,
    enabled: bool,
    sub_id: str,
    comment: str,
) -> dict:
    """
    Создаёт полный payload клиента для панели.
    
    IMPORTANT: Всегда отправляем полный объект, а не частичный.
    Панель может некорректно обрабатывать урезанные payload.
    """
    return {
        "clients": [{
            "id": client_uuid,
            "flow": "",
            "email": email,
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": expiry_ts,
            "enable": enabled,
            "tgId": str(user_id),
            "subId": sub_id,
            "comment": comment,
            "reset": 0,
        }]
    }


def _build_form_data(settings: dict) -> dict:
    """Формирует form data для запроса к панели."""
    return {
        "id": INBOUND_ID,
        "settings": json.dumps(settings),
    }


async def create_panel_client(user_id, username, days=1):
    """Создаёт основного клиента пользователя в панели."""
    logger.info(
        "panel.create_client.start user_id=%s username=%s days=%s",
        user_id,
        username,
        days,
    )

    client_uuid = str(uuid.uuid4())
    sub_id = _generate_sub_id()
    expiry_ts = _calc_expiry_ts(days)
    email = _build_main_client_email(user_id, client_uuid)

    client_settings = _build_client_settings(
        client_uuid=client_uuid,
        user_id=user_id,
        email=email,
        expiry_ts=expiry_ts,
        enabled=True,
        sub_id=sub_id,
        comment="Main client",
    )

    res = await panel_request(
        "POST",
        "/panel/api/inbounds/addClient",
        data=_build_form_data(client_settings),
    )

    logger.info(
        "panel.create_client.done user_id=%s success=%s msg=%s uuid=%s",
        user_id,
        res.get("success"),
        res.get("msg"),
        client_uuid,
    )

    if res.get("success"):
        link = build_vless_link(client_uuid, username)
        return link, client_uuid, None

    return None, None, f"Ошибка панели: {res.get('msg')}"


async def update_panel_client(client_uuid, user_id, username, target_ts_ms):
    """Продлевает срок действия основного клиента."""
    email = _build_main_client_email(user_id, client_uuid)

    client_settings = _build_client_settings(
        client_uuid=client_uuid,
        user_id=user_id,
        email=email,
        expiry_ts=target_ts_ms,
        enabled=True,
        sub_id="",
        comment="Renewed",
    )

    res = await panel_request(
        "POST",
        f"/panel/api/inbounds/updateClient/{client_uuid}",
        data=_build_form_data(client_settings),
        timeout=10,
    )

    if res.get("success"):
        logger.info(
            "panel.update_client.success user_id=%s uuid=%s target_ts_ms=%s",
            user_id,
            client_uuid,
            target_ts_ms,
        )
        return True, "Успешно"

    logger.error(
        "panel.update_client.fail user_id=%s uuid=%s msg=%s",
        user_id,
        client_uuid,
        res.get("msg"),
    )
    return False, res.get("msg")


async def add_device_to_panel(user_id, username, device_name, days=30):
    """Добавляет устройство пользователя в 3X-UI как отдельного клиента."""
    logger.info(
        "panel.add_device.start user_id=%s username=%s device_name=%s days=%s",
        user_id,
        username,
        device_name,
        days,
    )

    client_uuid = str(uuid.uuid4())
    sub_id = _generate_sub_id()
    expiry_ts = _calc_expiry_ts(days)
    email = _build_device_email(user_id, device_name, client_uuid)

    client_settings = _build_client_settings(
        client_uuid=client_uuid,
        user_id=user_id,
        email=email,
        expiry_ts=expiry_ts,
        enabled=True,
        sub_id=sub_id,
        comment=f"Device: {device_name} for user {user_id}",
    )

    res = await panel_request(
        "POST",
        "/panel/api/inbounds/addClient",
        data=_build_form_data(client_settings),
    )

    logger.info(
        "panel.add_device.done user_id=%s device_name=%s success=%s msg=%s uuid=%s",
        user_id,
        device_name,
        res.get("success"),
        res.get("msg"),
        client_uuid,
    )

    if res.get("success"):
        link = build_vless_link(client_uuid, device_name)
        return link, client_uuid, None

    return None, None, f"Ошибка панели: {res.get('msg')}"


async def remove_device_from_panel(client_uuid, user_id):
    """Удаляет устройство из 3X-UI."""
    logger.info("panel.remove_device.start user_id=%s uuid=%s", user_id, client_uuid)

    res = await panel_request(
        "POST",
        f"/panel/api/inbounds/{INBOUND_ID}/delClient/{client_uuid}",
        timeout=10,
    )

    if res.get("success") or "not found" in str(res.get("msg", "")).lower():
        logger.info("panel.remove_device.success user_id=%s uuid=%s", user_id, client_uuid)
        return True, None

    logger.warning(
        "panel.remove_device.fail user_id=%s uuid=%s msg=%s",
        user_id,
        client_uuid,
        res.get("msg"),
    )
    return False, res.get("msg")


async def activate_all_user_devices(user_id):
    """
    Активирует все устройства пользователя в панели.
    
    NOTE: Отправляем полный payload клиента, а не только id+enable.
    expiry_ts устанавливается в 0 (без ограничения), т.к. в БД не хранится.
    
    Возвращает (success_count, fail_count, errors)
    """
    from app.db import get_user_devices

    devices = await asyncio.to_thread(get_user_devices, user_id)
    if not devices:
        logger.info("panel.activate_devices.skip user_id=%s reason=no_devices", user_id)
        return 0, 0, []

    success_count = 0
    fail_count = 0
    errors = []

    for dev in devices:
        dev_name = dev["device_name"]
        client_uuid = dev["client_uuid"]

        try:
            email = _build_device_email(user_id, dev_name, client_uuid)
            
            client_settings = _build_client_settings(
                client_uuid=client_uuid,
                user_id=user_id,
                email=email,
                expiry_ts=0,  # 0 = без ограничения по времени
                enabled=True,
                sub_id="",
                comment=f"Device: {dev_name} for user {user_id}",
            )

            res = await panel_request(
                "POST",
                f"/panel/api/inbounds/updateClient/{client_uuid}",
                data=_build_form_data(client_settings),
                timeout=10,
            )

            if res.get("success"):
                logger.info(
                    "panel.activate_device.success user_id=%s device_name=%s uuid=%s",
                    user_id,
                    dev_name,
                    client_uuid,
                )
                success_count += 1
            else:
                err_msg = res.get("msg", "Неизвестная ошибка")
                logger.warning(
                    "panel.activate_device.fail user_id=%s device_name=%s uuid=%s msg=%s",
                    user_id,
                    dev_name,
                    client_uuid,
                    err_msg,
                )
                fail_count += 1
                errors.append((dev_name, err_msg))

        except Exception as e:
            logger.exception(
                "panel.activate_device.exception user_id=%s device_name=%s uuid=%s",
                user_id,
                dev_name,
                client_uuid,
            )
            fail_count += 1
            errors.append((dev_name, str(e)))

    logger.info(
        "panel.activate_devices.done user_id=%s success_count=%s fail_count=%s",
        user_id,
        success_count,
        fail_count,
    )
    return success_count, fail_count, errors


async def deactivate_all_user_devices(user_id):
    """
    Деактивирует все устройства пользователя в панели.
    
    NOTE: Отправляем полный payload клиента, а не только id+enable.
    expiry_ts устанавливается в 0 (без ограничения), т.к. в БД не хранится.
    
    Возвращает (success_count, fail_count)
    """
    from app.db import get_user_devices

    devices = await asyncio.to_thread(get_user_devices, user_id)
    if not devices:
        logger.info("panel.deactivate_devices.skip user_id=%s reason=no_devices", user_id)
        return 0, 0

    success_count = 0
    fail_count = 0

    for dev in devices:
        dev_name = dev["device_name"]
        client_uuid = dev["client_uuid"]

        try:
            email = _build_device_email(user_id, dev_name, client_uuid)
            
            client_settings = _build_client_settings(
                client_uuid=client_uuid,
                user_id=user_id,
                email=email,
                expiry_ts=0,  # 0 = без ограничения по времени
                enabled=False,
                sub_id="",
                comment=f"Device: {dev_name} for user {user_id}",
            )

            res = await panel_request(
                "POST",
                f"/panel/api/inbounds/updateClient/{client_uuid}",
                data=_build_form_data(client_settings),
                timeout=10,
            )

            if res.get("success"):
                logger.info(
                    "panel.deactivate_device.success user_id=%s device_name=%s uuid=%s",
                    user_id,
                    dev_name,
                    client_uuid,
                )
                success_count += 1
            else:
                logger.warning(
                    "panel.deactivate_device.fail user_id=%s device_name=%s uuid=%s msg=%s",
                    user_id,
                    dev_name,
                    client_uuid,
                    res.get("msg"),
                )
                fail_count += 1

        except Exception:
            logger.exception(
                "panel.deactivate_device.exception user_id=%s device_name=%s uuid=%s",
                user_id,
                dev_name,
                client_uuid,
            )
            fail_count += 1

    logger.info(
        "panel.deactivate_devices.done user_id=%s success_count=%s fail_count=%s",
        user_id,
        success_count,
        fail_count,
    )
    return success_count, fail_count


async def rotate_client_uuid(old_uuid, user_id, username, target_ts_ms):
    """
    Создаёт новый UUID для клиента, затем удаляет старый.
    Возвращает (success, new_uuid, message)
    """
    logger.info(
        "panel.rotate_client.start user_id=%s old_uuid=%s",
        user_id,
        old_uuid,
    )

    new_uuid = str(uuid.uuid4())
    sub_id = _generate_sub_id()
    email = _build_main_client_email(user_id, new_uuid)

    client_settings = _build_client_settings(
        client_uuid=new_uuid,
        user_id=user_id,
        email=email,
        expiry_ts=target_ts_ms,
        enabled=True,
        sub_id=sub_id,
        comment="Rotated Key",
    )

    res = await panel_request(
        "POST",
        "/panel/api/inbounds/addClient",
        data=_build_form_data(client_settings),
        timeout=10,
    )

    if not res.get("success"):
        logger.error(
            "panel.rotate_client.fail_create user_id=%s old_uuid=%s msg=%s",
            user_id,
            old_uuid,
            res.get("msg"),
        )
        return False, None, res.get("msg")

    await panel_request(
        "POST",
        f"/panel/api/inbounds/{INBOUND_ID}/delClient/{old_uuid}",
        timeout=10,
    )

    logger.info(
        "panel.rotate_client.success user_id=%s old_uuid=%s new_uuid=%s",
        user_id,
        old_uuid,
        new_uuid,
    )
    return True, new_uuid, "OK"