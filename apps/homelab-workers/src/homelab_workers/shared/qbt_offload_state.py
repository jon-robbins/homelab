from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


ALLOWED_STATUS_KEYS = {
    "updated_at",
    "last_run",
    "offloaded",
    "failed",
    "hdd_full",
    "pending_on_nvme",
}


def write_qbt_offload_status(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    sanitized = {key: payload[key] for key in ALLOWED_STATUS_KEYS if key in payload}
    file_descriptor, temporary_path = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(json.dumps(sanitized, separators=(",", ":"), sort_keys=True) + "\n")
        os.replace(temporary_path, target)
    finally:
        Path(temporary_path).unlink(missing_ok=True)


def read_qbt_offload_status(path: str | Path) -> dict[str, Any] | None:
    target = Path(path)
    if not target.exists():
        return None
    raw = target.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else None


def build_qbt_offload_status(
    *,
    last_run: int,
    offloaded: int,
    failed: int,
    hdd_full: bool,
    pending_on_nvme: int,
) -> dict[str, Any]:
    return {
        "updated_at": int(time.time()),
        "last_run": last_run,
        "offloaded": offloaded,
        "failed": failed,
        "hdd_full": hdd_full,
        "pending_on_nvme": pending_on_nvme,
    }


def evaluate_qbt_offload_health(
    status: dict[str, Any] | None,
    *,
    max_age_seconds: int,
    now: int | None = None,
) -> dict[str, Any]:
    if status is None:
        return {"healthy": False, "reason": "missing_status", "age_seconds": None}
    updated_at = status.get("updated_at")
    if not isinstance(updated_at, int):
        return {"healthy": False, "reason": "no_updated_at", "age_seconds": None}
    age = int(now if now is not None else time.time()) - updated_at
    if status.get("hdd_full"):
        return {"healthy": False, "reason": "hdd_full", "age_seconds": age}
    if age > max_age_seconds:
        return {"healthy": False, "reason": "stale_heartbeat", "age_seconds": age}
    if int(status.get("failed") or 0) > 0:
        return {"healthy": False, "reason": "last_run_failed", "age_seconds": age}
    return {"healthy": True, "reason": "ok", "age_seconds": age}
