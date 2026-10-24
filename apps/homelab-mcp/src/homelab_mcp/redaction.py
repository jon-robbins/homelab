from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SECRET_KEYS = {
    "api_key",
    "apikey",
    "api-key",
    "x-api-key",
    "x-api-token",
    "authorization",
    "auth",
    "bearer",
    "cookie",
    "pass",
    "passwd",
    "password",
    "passkey",
    "secret",
    "token",
}

SECRET_VALUE_REPLACEMENT = "[redacted]"

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|authorization|bearer|cookie|passkey|passwd|password|secret|token)=([^&\s\"'<>]+)"
)


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    query = urlencode(
        [
            (key, SECRET_VALUE_REPLACEMENT if key.lower() in SECRET_KEYS else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ],
        doseq=True,
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def redact_secret_string(value: str) -> str:
    redacted = _URL_RE.sub(_redact_url, value)
    redacted = _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}={SECRET_VALUE_REPLACEMENT}", redacted)
    redacted = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        f"Bearer {SECRET_VALUE_REPLACEMENT}",
        redacted,
    )
    return redacted


def redact_for_response(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secret_string(value)
    if isinstance(value, list):
        return [redact_for_response(item) for item in value]
    if isinstance(value, tuple):
        return [redact_for_response(item) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str.lower() in SECRET_KEYS:
                out[key_str] = SECRET_VALUE_REPLACEMENT
            else:
                out[key_str] = redact_for_response(item)
        return out
    return value


def tracker_url_summary(raw_url: object) -> dict[str, str | None]:
    raw = str(raw_url or "")
    try:
        parts = urlsplit(raw)
    except ValueError:
        return {"url": SECRET_VALUE_REPLACEMENT, "host": None}
    host = parts.hostname
    scheme = parts.scheme or None
    safe_url = f"{scheme}://{host}/[redacted]" if scheme and host else SECRET_VALUE_REPLACEMENT
    return {"url": safe_url, "host": host}
