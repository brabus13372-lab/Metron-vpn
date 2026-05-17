from urllib.parse import quote

from app.config import (
    SERVER_IP,
    VLESS_PORT,
    VLESS_TYPE,
    VLESS_SECURITY,
    VLESS_PBK,
    VLESS_FP,
    VLESS_SNI,
    VLESS_SID,
)


def build_vless_link(client_uuid, username):
    """Генерирует ссылку VLESS, корректно обрабатывая отсутствие SID."""
    sid_param = f"sid={quote(VLESS_SID, safe='')}&" if VLESS_SID else ""
    safe_name = quote(f"METRON_{username}", safe="")

    return (
        f"vless://{client_uuid}@{SERVER_IP}:{VLESS_PORT}?"
        f"type={quote(VLESS_TYPE, safe='')}&"
        f"security={quote(VLESS_SECURITY, safe='')}&"
        f"encryption=none&"
        f"pbk={quote(VLESS_PBK, safe='')}&"
        f"fp={quote(VLESS_FP, safe='')}&"
        f"sni={quote(VLESS_SNI, safe='')}&"
        f"{sid_param}spx=%2F&flow=#"
        f"{safe_name}"
    )