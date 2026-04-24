from __future__ import annotations

import re
from typing import Any, Optional


def human_size(n: int) -> str:
    n = int(n)
    if n < 0:
        n = 0
    for label, div in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if n >= div:
            return f"{n / div:.1f} {label}"
    return f"{n} B"


def indexer_name(raw: Any) -> str:
    v = raw.get("indexer")
    if isinstance(v, str) and v.strip():
        return v
    if isinstance(v, dict):
        return str(v.get("name") or v.get("id") or "unknown")
    return "unknown"


def int_field(x: Any) -> Optional[int]:
    if x is None:
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def fold_for_match(s: str) -> str:
    t = s.casefold()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())
