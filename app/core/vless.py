from urllib.parse import quote, urlencode

from app.config import (
    SERVER_IP,
    VLESS_PORT,
    VLESS_TYPE,
    VLESS_SECURITY,
    VLESS_PBK,
    VLESS_FP,
    VLESS_SNI,
    VLESS_SID,
    VLESS_SPX,
    VLESS_USE_FRAGMENT,
    VLESS_XHTTP_PATH,
    VLESS_XHTTP_HOST,
    VLESS_XHTTP_MODE,
)


def _is_xhttp_transport() -> bool:
    return VLESS_TYPE.strip().lower() in ("xhttp", "splithttp")


def build_vless_link(client_uuid, username, flow=None, spx=None):
    """
    Собирает VLESS URI под текущий inbound (tcp / xhttp + reality).

    v2RayTun ошибочно склеивает #remark с последним query-параметром (spx, mode…).
    По умолчанию fragment (#METRON_имя) не добавляем — имя задаётся в приложении вручную.
    """
    params: list[tuple[str, str]] = [
        ("type", VLESS_TYPE),
        ("encryption", "none"),
    ]

    if _is_xhttp_transport():
        if VLESS_XHTTP_PATH:
            params.append(("path", VLESS_XHTTP_PATH))
        params.append(("host", VLESS_XHTTP_HOST or ""))
        if VLESS_XHTTP_MODE:
            params.append(("mode", VLESS_XHTTP_MODE))
    elif flow:
        params.append(("flow", flow))

    params.extend([
        ("security", VLESS_SECURITY),
        ("pbk", VLESS_PBK),
        ("fp", VLESS_FP),
        ("sni", VLESS_SNI),
    ])

    if VLESS_SID:
        params.append(("sid", VLESS_SID))

    effective_spx = spx if spx is not None else VLESS_SPX
    if effective_spx and VLESS_SECURITY == "reality":
        params.append(("spx", effective_spx))

    query = urlencode(params, quote_via=quote)
    base = f"vless://{client_uuid}@{SERVER_IP}:{VLESS_PORT}?{query}"

    if not VLESS_USE_FRAGMENT:
        return base

    safe_name = quote(f"METRON_{username}", safe="")
    return f"{base}#{safe_name}"
