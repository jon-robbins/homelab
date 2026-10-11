from __future__ import annotations

import re

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)\S+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret|cookie)\s*[:=]\s*[\"']?)[^\s\"']+"),
    re.compile(r"cursor_[A-Za-z0-9_\-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
)


def redact_text(text: str) -> str:
    out = text
    for pat in _PATTERNS:
        if pat.groups:
            out = pat.sub(lambda m: f"{m.group(1)}[REDACTED]", out)
        else:
            out = pat.sub("[REDACTED]", out)
    return out
