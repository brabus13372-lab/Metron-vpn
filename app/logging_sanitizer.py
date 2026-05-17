import copy
import logging
import re
from typing import Iterable, Optional


MASK = "[MASKED]"


SENSITIVE_KEYS = {
    "password",
    "pass",
    "token",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "set-cookie",
    "key",
}


def _mask_value(value: str) -> str:
    if not value:
        return value
    if MASK in value:
        return value
    return MASK


class SensitiveDataFilter(logging.Filter):
    _tg_token_re = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
    _cookie_json_re = re.compile(r'(?i)("?(?:set-cookie|cookie|authorization)"?\s*:\s*)"([^"]*)"')
    _cookie_header_re = re.compile(r"(?i)\b(set-cookie|cookie|authorization)\b\s*:\s*([^\r\n]+)")
    _json_secret_re = re.compile(
        r'(?i)("?(?:password|pass|token|secret|api_key|apikey|access_token|refresh_token|authorization|key)"?\s*:\s*)"([^"]*)"'
    )
    _kv_secret_re = re.compile(
        r"(?i)\b(password|pass|token|secret|api_key|apikey|access_token|refresh_token|authorization|key)\b\s*=\s*([^&\s]+)"
    )

    def __init__(self, secrets: Optional[Iterable[str]] = None) -> None:
        super().__init__()
        self._secrets = [s for s in (secrets or []) if isinstance(s, str) and s and len(s) >= 4]

        if self._secrets:
            escaped = sorted({re.escape(s) for s in self._secrets}, key=len, reverse=True)
            self._literal_secrets_re = re.compile("|".join(escaped))
        else:
            self._literal_secrets_re = None

    def _sanitize_text(self, text: str) -> str:
        if not text:
            return text

        text = self._tg_token_re.sub(MASK, text)
        text = self._cookie_json_re.sub(lambda m: f'{m.group(1)}"{MASK}"', text)
        text = self._cookie_header_re.sub(lambda m: f"{m.group(1)}: {MASK}", text)
        text = self._json_secret_re.sub(lambda m: f'{m.group(1)}"{MASK}"', text)
        text = self._kv_secret_re.sub(lambda m: f"{m.group(1)}={MASK}", text)

        if self._literal_secrets_re:
            text = self._literal_secrets_re.sub(MASK, text)

        return text

    def _sanitize_obj(self, obj):
        try:
            if isinstance(obj, dict):
                out = {}
                for k, v in obj.items():
                    if isinstance(k, str) and k.lower() in SENSITIVE_KEYS:
                        out[k] = MASK
                    else:
                        out[k] = self._sanitize_obj(v)
                return out

            if isinstance(obj, list):
                return [self._sanitize_obj(x) for x in obj]

            if isinstance(obj, tuple):
                return tuple(self._sanitize_obj(x) for x in obj)

            if isinstance(obj, str):
                return self._sanitize_text(obj)

        except Exception:
            return obj

        return obj

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.msg
            args = copy.deepcopy(record.args)

            record.msg = self._sanitize_text(str(msg))
            record.args = self._sanitize_obj(args)

            if hasattr(record, "headers"):
                record.headers = self._sanitize_obj(copy.deepcopy(getattr(record, "headers")))

        except Exception:
            return True

        return True


def _collect_config_secrets() -> list[str]:
    try:
        from app import config
    except Exception:
        return []

    candidates = [
        getattr(config, "BOT_TOKEN", None),
        getattr(config, "PAY_TOKEN", None),
        getattr(config, "PANEL_USER", None),
        getattr(config, "PANEL_PASS", None),
        getattr(config, "VLESS_PBK", None),
        getattr(config, "VLESS_SID", None),
    ]
    return [c for c in candidates if isinstance(c, str) and c]


def ensure_sanitized_logging() -> None:
    root = logging.getLogger()

    for f in root.filters:
        if isinstance(f, SensitiveDataFilter):
            return

    flt = SensitiveDataFilter(secrets=_collect_config_secrets())

    root.addFilter(flt)
    for h in root.handlers:
        h.addFilter(flt)