from __future__ import annotations

import re
from typing import Any


def human_size(n: int) -> str:
    n = int(n)
    if n < 0:
        n = 0
    for label, div in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if n >= div:
            return f"{n / div:.1f} {label}"
    return f"{n} B"


def indexer_name(raw: Any) -> str:
    if isinstance(raw, dict):
        inner = raw.get("indexer")
        if isinstance(inner, str) and inner.strip():
            return inner
        if isinstance(inner, dict):
            return str(inner.get("name") or inner.get("id") or "unknown")
        return str(raw.get("indexerId") or "unknown")
    if isinstance(raw, str) and raw.strip():
        return raw
    return "unknown"


def int_field(x: Any) -> int | None:
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


def release_flag_fields(rel: dict) -> dict:
    flags = rel.get("indexerFlags") or []
    if not isinstance(flags, list):
        flags = []
    flag_strs = [str(f) for f in flags]
    rejections = rel.get("rejections") or []
    if not isinstance(rejections, list):
        rejections = []
    rej_strs = [str(r) for r in rejections[:5]]
    is_fl = any(f.lower() == "freeleech" for f in flag_strs)
    return {
        "indexer_flags": flag_strs,
        "is_freeleech": is_fl,
        "rejections": rej_strs,
    }
