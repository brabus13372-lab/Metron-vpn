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
    # Если когда-нибудь добавишь:
    # VLESS_FLOW,
    # VLESS_SPX,
)


def build_vless_link(client_uuid, username, flow=None, spx="/"):
    params = [
        ("type", VLESS_TYPE),
        ("encryption", "none"),
        ("security", VLESS_SECURITY),
        ("pbk", VLESS_PBK),
        ("fp", VLESS_FP),
        ("sni", VLESS_SNI),
    ]

    if VLESS_SID:
        params.append(("sid", VLESS_SID))

    if spx:
        params.append(("spx", spx))

    if flow:
        params.append(("flow", flow))

    query = urlencode(params, quote_via=quote)
    safe_name = quote(f"METRON_{username}", safe="")

    return f"vless://{client_uuid}@{SERVER_IP}:{VLESS_PORT}?{query}#{safe_name}"