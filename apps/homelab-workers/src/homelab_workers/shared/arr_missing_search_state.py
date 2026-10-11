from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


@contextmanager
def arr_missing_search_lock(lock_path: str | Path) -> Iterator[None]:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


ALLOWED_STATUS_KEYS = {
    "enabled",
    "running",
    "ok",
    "dry_run",
    "last_started_at",
    "last_completed_at",
    "next_scheduled_at",
    "sonarr_missing_total",
    "radarr_missing_total",
    "sonarr_searched",
    "radarr_searched",
    "consecutive_failures",
    "last_error",
    "updated_at",
}


def write_missing_search_status(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    sanitized = {key: payload[key] for key in ALLOWED_STATUS_KEYS if key in payload}
    encoded = json.dumps(sanitized, separators=(",", ":"), sort_keys=True)
    temp.write_text(encoded + "\n", encoding="utf-8")
    os.replace(temp, target)


def read_missing_search_status(path: str | Path) -> dict[str, Any] | None:
    target = Path(path)
    if not target.exists():
        return None
    raw = target.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else None


def build_missing_search_status(
    *,
    enabled: bool,
    running: bool,
    ok: bool,
    started_at: int,
    completed_at: int | None,
    next_scheduled_at: int | None,
    sonarr_missing_total: int,
    radarr_missing_total: int,
    sonarr_searched: int,
    radarr_searched: int,
    consecutive_failures: int,
    last_error: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "running": running,
        "ok": ok,
        "dry_run": dry_run,
        "last_started_at": started_at,
        "last_completed_at": completed_at,
        "next_scheduled_at": next_scheduled_at,
        "sonarr_missing_total": sonarr_missing_total,
        "radarr_missing_total": radarr_missing_total,
        "sonarr_searched": sonarr_searched,
        "radarr_searched": radarr_searched,
        "consecutive_failures": consecutive_failures,
        "last_error": last_error,
        "updated_at": int(time.time()),
    }


def evaluate_missing_search_health(
    status: dict[str, Any] | None,
    *,
    max_age_seconds: int,
    now: int | None = None,
) -> dict[str, Any]:
    ts = int(now if now is not None else time.time())
    if status is None:
        return {"healthy": False, "reason": "missing_status", "age_seconds": None}
    if not status.get("enabled"):
        return {"healthy": True, "reason": "disabled", "age_seconds": None}
    updated_at = status.get("updated_at")
    if not isinstance(updated_at, int):
        return {"healthy": False, "reason": "no_updated_at", "age_seconds": None}
    age = ts - updated_at
    if age > max_age_seconds:
        return {
            "healthy": False,
            "reason": "stale_heartbeat",
            "age_seconds": age,
        }
    if not status.get("ok"):
        return {
            "healthy": False,
            "reason": "last_cycle_failed",
            "age_seconds": age,
            "last_error": status.get("last_error"),
        }
    return {"healthy": True, "reason": "ok", "age_seconds": age}
