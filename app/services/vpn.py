import json
import logging
import random
import string
import uuid

from datetime import datetime, timedelta

from app.config import INBOUND_ID
from app.core.vless import build_vless_link
from app.core.panel_client import (
    panel_request,
    update_client_fields,
    get_inbound,
    parse_inbound_settings,
    find_client_in_settings,
)
from app.db import (
    activate_device,
    deactivate_device,
    get_user_data_dict,
    get_user_devices,
    update_device_link,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PANEL_NO_EXPIRY = 0
PANEL_FALLBACK_EXPIRY_DAYS = 3650  # fallback if panel breaks on expiryTime=0


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _generate_sub_id(length: int = 16) -> str:
    """Generates a random subId for a panel client."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _build_main_client_email(user_id: int, client_uuid: str) -> str:
    """Builds email for the main (per-user) panel client."""
    return f"metron_{user_id}_{client_uuid[:8]}"


def _build_device_email(user_id: int, device_name: str, client_uuid: str) -> str:
    """Builds email for a per-device panel client."""
    safe_device = (device_name or "device").strip().replace(" ", "_")[:20]
    return f"TG_{user_id}_{safe_device}_{client_uuid[:8]}"


def _calc_expiry_ts(days: int | None = None) -> int:
    """
    Converts days into a millisecond timestamp for the panel.
    Returns 0 (no expiry) when days is None or <= 0.
    """
    if days is None or days <= 0:
        return PANEL_NO_EXPIRY
    return int((datetime.now() + timedelta(days=days)).timestamp() * 1000)


def _panel_expiry_ts_for_dynamic_access() -> int:
    """
    Returns the expiry timestamp used for dynamic-balance clients.
    Business logic does NOT rely on panel expiry — balance is the source of truth.
    Switch to _calc_expiry_ts(PANEL_FALLBACK_EXPIRY_DAYS) if panel breaks on 0.
    """
    return PANEL_NO_EXPIRY


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
    Builds a full client payload for the panel.
    Always sends a complete object — the panel may mishandle partial payloads.
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
    """Wraps settings dict into the form data expected by the panel API."""
    return {
        "id": INBOUND_ID,
        "settings": json.dumps(settings),
    }


# ---------------------------------------------------------------------------
# Panel client CRUD
# ---------------------------------------------------------------------------

async def create_panel_client(user_id: int, username: str):
    """
    Creates the main client in the panel for dynamic-balance access.
    Returns (vless_link, client_uuid, error_message).
    """
    logger.info("panel.create_client.start user_id=%s username=%s", user_id, username)

    client_uuid = str(uuid.uuid4())
    sub_id = _generate_sub_id()
    expiry_ts = _panel_expiry_ts_for_dynamic_access()
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
        "panel.create_client.done user_id=%s success=%s msg=%s uuid=%s expiry_ts=%s",
        user_id, res.get("success"), res.get("msg"), client_uuid, expiry_ts,
    )

    if res.get("success"):
        link = build_vless_link(client_uuid, username)
        return link, client_uuid, None

    return None, None, f"Panel error: {res.get('msg')}"


async def update_panel_client(client_uuid: str, user_id: int, username: str, target_ts_ms: int):
    """
    Updates expiry time and re-enables an existing panel client.
    Returns (success, message).
    """
    ok, msg = await update_client_fields(
        client_uuid,
        {"expiryTime": target_ts_ms, "enable": True},
        inbound_id=INBOUND_ID,
    )

    if ok:
        logger.info(
            "panel.update_client.success user_id=%s uuid=%s target_ts_ms=%s",
            user_id, client_uuid, target_ts_ms,
        )
        return True, "OK"

    logger.error(
        "panel.update_client.fail user_id=%s uuid=%s msg=%s",
        user_id, client_uuid, msg,
    )
    return False, msg


async def add_device_to_panel(user_id: int, username: str, device_name: str):
    """
    Adds a per-device client in the panel for dynamic-balance access.
    Returns (vless_link, client_uuid, error_message).
    """
    logger.info(
        "panel.add_device.start user_id=%s username=%s device_name=%s",
        user_id, username, device_name,
    )

    client_uuid = str(uuid.uuid4())
    sub_id = _generate_sub_id()
    expiry_ts = _panel_expiry_ts_for_dynamic_access()
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
        "panel.add_device.done user_id=%s device_name=%s success=%s msg=%s uuid=%s expiry_ts=%s",
        user_id, device_name, res.get("success"), res.get("msg"), client_uuid, expiry_ts,
    )

    if res.get("success"):
        link = build_vless_link(client_uuid, device_name)
        return link, client_uuid, None

    return None, None, f"Panel error: {res.get('msg')}"


async def remove_device_from_panel(client_uuid: str, user_id: int):
    """
    Removes a device client from the panel.
    Returns (success, error_message).
    """
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
        user_id, client_uuid, res.get("msg"),
    )
    return False, res.get("msg")


# ---------------------------------------------------------------------------
# Bulk activate / deactivate
# ---------------------------------------------------------------------------

async def activate_all_user_devices(user_id: int):
    """
    Enables panel clients for the given user and only marks successful rows
    as active in the DB.

    Devices disabled voluntarily by the user (disabled_reason='user_request')
    are intentionally skipped — the user chose to turn them off and that choice
    must be preserved after a topup.

    Returns (success_count, fail_count, errors).
    """
    devices = await get_user_devices(user_id)
    if not devices:
        logger.info("panel.activate_devices.skip user_id=%s reason=no_devices", user_id)
        return 0, 0, []

    user = await get_user_data_dict(user_id)
    username = (user or {}).get("username") or f"user_{user_id}"
    success_count = 0
    fail_count = 0
    errors = []

    for dev in devices:
        # Skip devices the user manually disabled — preserve their choice.
        if dev.get("disabled_reason") == "user_request":
            logger.info(
                "panel.activate_device.skip user_id=%s device=%s reason=user_request",
                user_id, dev["device_name"],
            )
            continue

        dev_name = dev["device_name"]
        client_uuid = dev["client_uuid"]
        try:
            ok, err_msg = await update_client_fields(
                client_uuid,
                {"enable": True},
                inbound_id=INBOUND_ID,
            )
            if ok:
                db_updated = await activate_device(dev["id"], user_id)
                if not db_updated:
                    logger.error(
                        "panel.activate_device.db_sync_fail user_id=%s device=%s uuid=%s reason=device_not_found",
                        user_id, dev_name, client_uuid,
                    )
                    fail_count += 1
                    errors.append((dev_name, "DB sync failed after panel activation"))
                    continue

                logger.info(
                    "panel.activate_device.success user_id=%s device=%s uuid=%s",
                    user_id, dev_name, client_uuid,
                )
                success_count += 1
            else:
                err_msg = err_msg or "Unknown error"
                if "client not found" in err_msg.lower():
                    logger.warning(
                        "panel.activate_device.missing user_id=%s device=%s uuid=%s — recreating",
                        user_id, dev_name, client_uuid,
                    )
                    new_link, new_uuid, recreate_err = await add_device_to_panel(
                        user_id=user_id,
                        username=username,
                        device_name=dev_name,
                    )
                    if recreate_err or not new_link or not new_uuid:
                        recreate_err = recreate_err or "Failed to recreate missing panel client"
                        logger.error(
                            "panel.activate_device.recreate_fail user_id=%s device=%s old_uuid=%s msg=%s",
                            user_id, dev_name, client_uuid, recreate_err,
                        )
                        fail_count += 1
                        errors.append((dev_name, recreate_err))
                        continue

                    db_link_updated = await update_device_link(
                        dev["id"],
                        user_id,
                        new_uuid,
                        new_link,
                    )
                    if not db_link_updated:
                        logger.error(
                            "panel.activate_device.recreate_db_sync_fail user_id=%s device=%s old_uuid=%s new_uuid=%s",
                            user_id, dev_name, client_uuid, new_uuid,
                        )
                        fail_count += 1
                        errors.append((dev_name, "DB sync failed after client recreation"))
                        continue

                    db_activated = await activate_device(dev["id"], user_id)
                    if not db_activated:
                        logger.error(
                            "panel.activate_device.recreate_db_activate_fail user_id=%s device=%s new_uuid=%s",
                            user_id, dev_name, new_uuid,
                        )
                        fail_count += 1
                        errors.append((dev_name, "DB activation failed after client recreation"))
                        continue

                    logger.warning(
                        "panel.activate_device.recreate_ok user_id=%s device=%s old_uuid=%s new_uuid=%s",
                        user_id, dev_name, client_uuid, new_uuid,
                    )
                    success_count += 1
                    continue

                logger.warning(
                    "panel.activate_device.fail user_id=%s device=%s uuid=%s msg=%s",
                    user_id, dev_name, client_uuid, err_msg,
                )
                fail_count += 1
                errors.append((dev_name, err_msg))
        except Exception as exc:
            logger.exception(
                "panel.activate_device.exception user_id=%s device=%s uuid=%s",
                user_id, dev_name, client_uuid,
            )
            fail_count += 1
            errors.append((dev_name, str(exc)))

    logger.info(
        "panel.activate_devices.done user_id=%s ok=%s fail=%s",
        user_id, success_count, fail_count,
    )
    return success_count, fail_count, errors


async def deactivate_all_user_devices(user_id: int, reason: str = "insufficient_funds"):
    """
    Disables panel clients for the given user and only marks rows inactive in
    the DB after the panel call succeeds.

    Manually disabled devices are left untouched in the DB so we do not
    overwrite `disabled_reason='user_request'`, but we still allow billing and
    reconcile flows to revoke active access for every active device.

    Returns (success_count, fail_count).
    """
    devices = await get_user_devices(user_id)
    if not devices:
        logger.info("panel.deactivate_devices.skip user_id=%s reason=no_devices", user_id)
        return 0, 0

    success_count = 0
    fail_count = 0

    for dev in devices:
        # Preserve user intent for devices that were already disabled manually.
        if not dev.get("is_active", True) and dev.get("disabled_reason") == "user_request":
            logger.info(
                "panel.deactivate_device.skip user_id=%s device=%s reason=user_request",
                user_id, dev["device_name"],
            )
            continue

        dev_name = dev["device_name"]
        client_uuid = dev["client_uuid"]
        was_active_in_db = bool(dev.get("is_active", False))
        try:
            ok, err_msg = await update_client_fields(
                client_uuid,
                {"enable": False},
                inbound_id=INBOUND_ID,
            )
            if ok:
                if was_active_in_db:
                    db_updated = await deactivate_device(dev["id"], user_id, reason=reason)
                    if not db_updated:
                        logger.error(
                            "panel.deactivate_device.db_sync_fail user_id=%s device=%s uuid=%s reason=device_not_found",
                            user_id, dev_name, client_uuid,
                        )
                        fail_count += 1
                        continue

                logger.info(
                    "panel.deactivate_device.success user_id=%s device=%s uuid=%s db_was_active=%s",
                    user_id, dev_name, client_uuid, was_active_in_db,
                )
                success_count += 1
            else:
                err_msg = err_msg or "Unknown error"
                logger.warning(
                    "panel.deactivate_device.fail user_id=%s device=%s uuid=%s msg=%s",
                    user_id, dev_name, client_uuid, err_msg,
                )
                fail_count += 1
        except Exception as exc:
            logger.exception(
                "panel.deactivate_device.exception user_id=%s device=%s uuid=%s",
                user_id, dev_name, client_uuid,
            )
            fail_count += 1

    logger.info(
        "panel.deactivate_devices.done user_id=%s ok=%s fail=%s",
        user_id, success_count, fail_count,
    )
    return success_count, fail_count


# ---------------------------------------------------------------------------
# Rotate UUID helpers
# ---------------------------------------------------------------------------

async def rotate_client_uuid(
    old_uuid: str,
    user_id: int,
    username: str,
) -> tuple[str | None, str | None, str | None]:
    """
    Low-level: rotates a single client UUID in the panel.
    Fetches current client settings, replaces UUID, updates in place.
    Returns (new_uuid, vless_link, error_message).
    """
    inbound = await get_inbound(INBOUND_ID)
    if not inbound:
        return None, None, "Failed to fetch inbound"

    settings = parse_inbound_settings(inbound)
    client = find_client_in_settings(settings, old_uuid)

    if not client:
        logger.warning(
            "rotate_client_uuid.not_found user_id=%s old_uuid=%s",
            user_id, old_uuid,
        )
        return None, None, f"Client {old_uuid} not found in panel"

    new_uuid = str(uuid.uuid4())

    ok, msg = await update_client_fields(
        old_uuid,
        {"id": new_uuid},
        inbound_id=INBOUND_ID,
    )

    if not ok:
        return None, None, f"Panel update failed: {msg}"

    link = build_vless_link(new_uuid, username)
    logger.info(
        "rotate_client_uuid.success user_id=%s old=%s new=%s",
        user_id, old_uuid, new_uuid,
    )
    return new_uuid, link, None


# ---------------------------------------------------------------------------
# High-level rotate (used by API)
# ---------------------------------------------------------------------------

async def rotate_user_key(
    user_id: int,
    username: str,
    old_uuid: str | None,
) -> tuple[str | None, str | None, str | None]:
    """
    Rotates the main VLESS key for a user.
    If old_uuid is missing, creates a brand-new client in the panel.
    Returns (vless_link, new_uuid, error_message).
    """
    if not old_uuid:
        logger.info(
            "rotate_user_key.no_old_uuid user_id=%s — creating new client",
            user_id,
        )
        vless_link, new_uuid, err = await create_panel_client(user_id, username)
        return vless_link, new_uuid, err

    new_uuid, vless_link, err = await rotate_client_uuid(old_uuid, user_id, username)
    if err:
        if "not found in panel" in str(err).lower():
            logger.warning(
                "rotate_user_key.rotate_missing user_id=%s err=%s — falling back to create",
                user_id, err,
            )
            vless_link, new_uuid, err = await create_panel_client(user_id, username)
        else:
            logger.error(
                "rotate_user_key.rotate_failed user_id=%s err=%s",
                user_id, err,
            )
            return None, None, err

    return vless_link, new_uuid, err
