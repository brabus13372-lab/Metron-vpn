import asyncio
import json
import logging
import ssl
import traceback
from urllib.parse import urlparse

from app.config import INBOUND_ID, PANEL_PASS, PANEL_URL, PANEL_USER

import aiohttp

DEFAULT_PANEL_TIMEOUT = aiohttp.ClientTimeout(total=10)


panel_session = None
panel_session_lock = asyncio.Lock()
session_cookies = None


#=== ИСПРАВЛЕННАЯ get_panel_session ===
async def get_panel_session():
    global panel_session, session_cookies

    if panel_session and not panel_session.closed:
        return panel_session

    async with panel_session_lock:
        if panel_session and not panel_session.closed:
            return panel_session

        connector = aiohttp.TCPConnector(ssl=False, force_close=True)
        session = aiohttp.ClientSession(connector=connector)
    login_url = f"{PANEL_URL}/login"
    
    try:
        async with session.post(login_url, data={"username": PANEL_USER, "password": PANEL_PASS}, timeout=10) as resp:
            data = await resp.json()
            # Смотрим все установленные куки
            set_cookie_headers = resp.headers.getall('Set-Cookie')

            if resp.status == 200 and data.get("success"):
                # Собираем строку кук вручную (ключ=значение из каждого заголовка)
                cookies_parts = []
                for raw_cookie in set_cookie_headers:
                    # Берём только первую часть до точки с запятой (key=value)
                    key_val = raw_cookie.split(';', 1)[0]
                    cookies_parts.append(key_val)
                session_cookies = "; ".join(cookies_parts)
                panel_session = session
                return session
            else:
                logging.error(f"❌ Ошибка авторизации: {data}")
                await session.close()
                return None
    except Exception as e:
        logging.error(f"💥 Ошибка подключения к панели: {e}\n{traceback.format_exc()}")
        await session.close()
        return None


def inject_csrf_token(session, kwargs):
    """Добавляет CSRF-токен в заголовки запроса."""
    try:
        csrf = session.cookie_jar.filter_cookies(PANEL_URL).get("csrf_token")
        if csrf:
            kwargs.setdefault("headers", {})["X-CSRF-TOKEN"] = csrf.value
    except Exception:
        pass


# === ИСПРАВЛЕННАЯ panel_request ===
async def deactivate_client(client_uuid):
    """
    Деактивирует клиента в панели (без удаления).
    Просто ставит enable=False для клиента.
    """
    logging.info(f"🔌 Деактивация клиента {client_uuid} в панели")

    client_settings = {
        "clients": [{
            "id": client_uuid,
            "enable": False
        }]
    }

    form_data = {
        "id": INBOUND_ID,
        "settings": json.dumps(client_settings)
    }

    res = await panel_request(
        "POST",
        f"/panel/api/inbounds/updateClient/{client_uuid}",
        data=form_data,
        timeout=10
    )

    if res.get("success"):
        logging.info(f"✅ Клиент {client_uuid} успешно деактивирован")
        return True, None
    else:
        logging.error(f"❌ Не удалось деактивировать клиента {client_uuid}: {res.get('msg')}")
        return False, res.get("msg")


async def panel_request(method, endpoint, **kwargs):
    global panel_session, session_cookies

    session = await get_panel_session()
    if not session:
        return {"success": False, "msg": "Нет соединения с панелью"}

    # Safety: never allow infinite hangs on panel calls
    if "timeout" not in kwargs or kwargs.get("timeout") is None:
        kwargs["timeout"] = DEFAULT_PANEL_TIMEOUT

    url = f"{PANEL_URL}/{endpoint.lstrip('/')}"
    parsed_url = urlparse(PANEL_URL)
    host = parsed_url.netloc

    # SSL-контекст (самоподписанный сертификат)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    # Базовые заголовки браузера + кука, если есть
    base_headers = {
        "Host": host,
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": f"{parsed_url.scheme}://{host}",
        "Referer": f"{PANEL_URL}/panel/inbounds",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Priority": "u=0",
    }

    # 🔥 Добавляем сохранённую куку
    if session_cookies:
        base_headers["Cookie"] = session_cookies

    headers = base_headers.copy()
    headers.update(kwargs.get("headers", {}))
    kwargs["headers"] = headers

    inject_csrf_token(session, kwargs)

    logging.info(f"📡 [OUT] {method} {url}")

    try:
        async with session.request(method, url, **kwargs) as resp:
            resp_text = await resp.text()
            logging.info(f"📥 [IN] Статус: {resp.status}")
            logging.info(f"📄 [BODY]: {resp_text[:500]}")

            if resp.status == 401:
                logging.warning("⚠️ Сессия истекла, перелогиниваемся...")
                async with panel_session_lock:
                    if panel_session:
                        await panel_session.close()
                    panel_session = None
                    session = await get_panel_session()
                    # Обновляем заголовок с новой кукой
                    if session_cookies:
                        headers["Cookie"] = session_cookies
                    kwargs["headers"] = headers
                    inject_csrf_token(session, kwargs)
                logging.info(f"📡 [OUT] (retry) {method} {url}")
                async with session.request(method, url, **kwargs) as retry_resp:
                    retry_text = await retry_resp.text()
                    logging.info(f"📥 [IN] Статус: {retry_resp.status}")
                    logging.info(f"📄 [BODY]: {retry_text[:500]}")
                    try:
                        return json.loads(retry_text)
                    except:
                        return {"success": retry_resp.status == 200, "msg": retry_text}

            try:
                return json.loads(resp_text)
            except:
                return {"success": resp.status == 200, "msg": resp_text}

    except Exception as e:
        logging.error(f"💥 Исключение: {e}\n{traceback.format_exc()}")
        return {"success": False, "msg": str(e)}

