import asyncio
import json
import logging
import traceback
from typing import Any
from urllib.parse import urlparse

import aiohttp

from app.config import INBOUND_ID, PANEL_PASS, PANEL_URL, PANEL_USER


DEFAULT_PANEL_TIMEOUT = aiohttp.ClientTimeout(total=10)

panel_session: aiohttp.ClientSession | None = None
panel_session_lock = asyncio.Lock()
manual_cookie_header: str | None = None


def _build_connector() -> aiohttp.TCPConnector:
    # WARNING: TLS verification is disabled (ssl=False).
    # This is acceptable ONLY when the bot and the panel run on the same host
    # or the same trusted private network and you cannot provision a valid certificate.
    # Even in that scenario it is strongly discouraged — a self-signed cert with
    # ssl_context would be significantly safer. Never use ssl=False in production
    # when the panel is reachable over the public internet.
    return aiohttp.TCPConnector(ssl=False)


def _build_cookie_jar() -> aiohttp.CookieJar:
    # unsafe=True is required for panels served over an IP address rather
    # than a proper domain, because aiohttp normally rejects cookies without
    # a domain attribute on IP-based origins.
    return aiohttp.CookieJar(unsafe=True)


def _base_headers() -> dict[str, str]:
    parsed_url = urlparse(PANEL_URL)
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:150.0) "
            "Gecko/20100101 Firefox/150.0"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": f"{PANEL_URL}/panel/inbounds",
    }


def _extract_manual_cookie_header(resp: aiohttp.ClientResponse) -> str | None:
    try:
        raw_set_cookie = resp.headers.getall("Set-Cookie", [])
    except Exception:
        raw_set_cookie = []

    cookie_parts: list[str] = []
    for raw_cookie in raw_set_cookie:
        key_val = raw_cookie.split(";", 1)[0].strip()
        if key_val and "=" in key_val:
            cookie_parts.append(key_val)

    return "; ".join(cookie_parts) if cookie_parts else None


def _log_cookie_diagnostics(session: aiohttp.ClientSession, source: str) -> None:
    try:
        cookies = session.cookie_jar.filter_cookies(PANEL_URL)
        cookie_names = sorted(cookies.keys())
        logging.debug(
            "Panel cookie diagnostics [%s]: jar_cookies=%s manual_cookie_present=%s",
            source,
            cookie_names,
            bool(manual_cookie_header),
        )
    except Exception:
        logging.debug("Could not read cookie diagnostics", exc_info=True)


def _inject_csrf_token(session: aiohttp.ClientSession, headers: dict[str, str]) -> None:
    try:
        cookies = session.cookie_jar.filter_cookies(PANEL_URL)
        csrf = cookies.get("csrf_token")
        if csrf and csrf.value:
            headers["X-CSRF-TOKEN"] = csrf.value
            return
    except Exception:
        logging.debug("Could not read csrf_token from cookie_jar", exc_info=True)

    # Fallback: extract csrf_token from the manually stored cookie string.
    if manual_cookie_header:
        for part in (p.strip() for p in manual_cookie_header.split(";")):
            if part.startswith("csrf_token="):
                headers["X-CSRF-TOKEN"] = part.split("=", 1)[1]
                return


def _inject_manual_cookie_header(headers: dict[str, str]) -> None:
    if manual_cookie_header:
        headers["Cookie"] = manual_cookie_header


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

async def _login_panel(session: aiohttp.ClientSession) -> None:
    global manual_cookie_header

    login_url = f"{PANEL_URL}/login"
    payload = {"username": PANEL_USER, "password": PANEL_PASS}

    logging.info("Logging in to 3x-ui panel...")
    async with session.post(login_url, data=payload) as resp:
        text = await resp.text()

        extracted = _extract_manual_cookie_header(resp)
        if extracted:
            manual_cookie_header = extracted

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"raw": text[:300]}

        _log_cookie_diagnostics(session, "after-login")

        if resp.status != 200 or not isinstance(data, dict) or not data.get("success"):
            raise RuntimeError(
                f"Panel login failed: status={resp.status}, body={data}"
            )

    logging.info("3x-ui panel login successful")


async def get_panel_session(force_relogin: bool = False) -> aiohttp.ClientSession:
    global panel_session, manual_cookie_header

    # Fast path: session exists and is healthy.
    if not force_relogin and panel_session and not panel_session.closed:
        return panel_session

    async with panel_session_lock:
        # Re-check inside the lock to avoid double-init.
        if not force_relogin and panel_session and not panel_session.closed:
            return panel_session

        old_session = panel_session
        panel_session = None
        if force_relogin:
            manual_cookie_header = None

        if old_session and not old_session.closed:
            await old_session.close()

        session = aiohttp.ClientSession(
            connector=_build_connector(),
            cookie_jar=_build_cookie_jar(),
            timeout=DEFAULT_PANEL_TIMEOUT,
            headers=_base_headers(),
        )

        try:
            await _login_panel(session)
        except Exception:
            await session.close()
            raise

        panel_session = session
        return session


async def close_panel_session() -> None:
    global panel_session, manual_cookie_header

    async with panel_session_lock:
        if panel_session and not panel_session.closed:
            await panel_session.close()
            await asyncio.sleep(0)
        panel_session = None
        manual_cookie_header = None


# ---------------------------------------------------------------------------
# Core request helper
# ---------------------------------------------------------------------------

async def _read_response(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    text = await resp.text()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        return {"success": resp.status == 200, "data": data}
    except json.JSONDecodeError:
        return {"success": resp.status == 200, "msg": text}


async def panel_request(
    method: str,
    endpoint: str,
    retry_on_401: bool = True,
    use_manual_cookie_fallback: bool = True,
    **kwargs,
) -> dict[str, Any]:
    url = f"{PANEL_URL}/{endpoint.lstrip('/')}"

    if "timeout" not in kwargs or kwargs["timeout"] is None:
        kwargs["timeout"] = DEFAULT_PANEL_TIMEOUT

    session = await get_panel_session()

    headers = dict(kwargs.pop("headers", {}) or {})
    _inject_csrf_token(session, headers)
    if use_manual_cookie_fallback:
        _inject_manual_cookie_header(headers)

    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and "json" not in kwargs:
        headers.setdefault(
            "Content-Type", "application/x-www-form-urlencoded; charset=UTF-8"
        )

    kwargs["headers"] = headers

    logging.info("Panel request: %s %s", method.upper(), url)

    try:
        async with session.request(method.upper(), url, **kwargs) as resp:
            result = await _read_response(resp)

            if resp.status in (401, 403) and retry_on_401:
                logging.warning(
                    "Panel auth issue (status=%s), relogin and retry endpoint=%s",
                    resp.status,
                    endpoint,
                )
                session = await get_panel_session(force_relogin=True)

                retry_headers = dict(kwargs.get("headers", {}))
                _inject_csrf_token(session, retry_headers)
                if use_manual_cookie_fallback:
                    _inject_manual_cookie_header(retry_headers)
                kwargs["headers"] = retry_headers

                async with session.request(method.upper(), url, **kwargs) as retry_resp:
                    retry_result = await _read_response(retry_resp)
                    if retry_resp.status >= 400:
                        logging.error(
                            "Panel retry request failed: status=%s endpoint=%s body=%s",
                            retry_resp.status,
                            endpoint,
                            str(retry_result)[:300],
                        )
                    return retry_result

            if resp.status >= 400:
                logging.error(
                    "Panel request failed: status=%s endpoint=%s body=%s",
                    resp.status,
                    endpoint,
                    str(result)[:300],
                )

            return result

    except asyncio.TimeoutError:
        logging.error("Timeout on panel endpoint: %s %s", method.upper(), endpoint)
        return {"success": False, "msg": "Panel request timeout"}
    except aiohttp.ClientError as exc:
        logging.error(
            "aiohttp error on panel request %s %s: %s", method.upper(), endpoint, exc
        )
        return {"success": False, "msg": f"Panel HTTP error: {exc}"}
    except Exception as exc:
        logging.error(
            "Unexpected exception on panel request %s %s: %s\n%s",
            method.upper(),
            endpoint,
            exc,
            traceback.format_exc(),
        )
        return {"success": False, "msg": str(exc)}


# ---------------------------------------------------------------------------
# High-level panel helpers
# ---------------------------------------------------------------------------

async def get_inbound(inbound_id: int = INBOUND_ID) -> dict[str, Any] | None:
    result = await panel_request("GET", f"/panel/api/inbounds/get/{inbound_id}")
    if not result.get("success"):
        return None
    obj = result.get("obj")
    return obj if isinstance(obj, dict) else None


def parse_inbound_settings(inbound: dict[str, Any]) -> dict[str, Any]:
    raw = inbound.get("settings")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def find_client_in_settings(
    settings: dict[str, Any], client_uuid: str
) -> dict[str, Any] | None:
    clients = settings.get("clients")
    if not isinstance(clients, list):
        return None
    for client in clients:
        if isinstance(client, dict) and str(client.get("id")) == str(client_uuid):
            return client
    return None


async def update_client_fields(
    client_uuid: str,
    patch: dict[str, Any],
    inbound_id: int = INBOUND_ID,
) -> tuple[bool, str | None]:
    """
    Reads the current client from the inbound, merges `patch` into it,
    and sends the full updated object back to the panel.
    Only the keys present in `patch` are changed; all other fields are preserved.
    """
    inbound = await get_inbound(inbound_id)
    if not inbound:
        return False, "Inbound not found"

    settings = parse_inbound_settings(inbound)
    existing = find_client_in_settings(settings, client_uuid)
    if not existing:
        return False, f"Client not found: {client_uuid}"

    updated_client = {**existing, **patch}

    form_data = {
        "id": inbound_id,
        "settings": json.dumps({"clients": [updated_client]}, ensure_ascii=False),
    }

    result = await panel_request(
        "POST",
        f"/panel/api/inbounds/updateClient/{client_uuid}",
        data=form_data,
        timeout=10,
    )

    if result.get("success"):
        return True, None

    return False, result.get("msg") or str(result)


async def deactivate_client(
    client_uuid: str,
    inbound_id: int = INBOUND_ID,
) -> tuple[bool, str | None]:
    """
    Disables a single panel client by setting enable=False.
    Uses update_client_fields so all other client fields are preserved.
    """
    logging.info("Deactivating client %s in panel (inbound_id=%s)", client_uuid, inbound_id)

    ok, msg = await update_client_fields(
        client_uuid,
        {"enable": False},
        inbound_id=inbound_id,
    )

    if ok:
        logging.info("Client %s deactivated successfully", client_uuid)
        return True, None

    logging.error("Failed to deactivate client %s: %s", client_uuid, msg)
    return False, msg