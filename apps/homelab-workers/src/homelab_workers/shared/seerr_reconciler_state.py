from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def read_reconciler_state(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {"requests": {}}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"requests": {}}
    return payload if isinstance(payload, dict) else {"requests": {}}


def write_reconciler_state(path: str, payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def build_reconciler_status(
    *,
    enabled: bool,
    fallback_enabled: bool,
    running: bool,
    ok: bool,
    dry_run: bool,
    started_at: int,
    completed_at: int | None,
    diagnosed: int,
    mutated: int,
    blocked: int,
    last_error: str | None = None,
) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "fallback_enabled": fallback_enabled,
        "running": running,
        "ok": ok,
        "dry_run": dry_run,
        "last_started_at": started_at,
        "last_completed_at": completed_at,
        "diagnosed": diagnosed,
        "mutated": mutated,
        "blocked": blocked,
        "last_error": last_error,
    }


def evaluate_reconciler_health(
    status: dict[str, Any] | None,
    max_age_seconds: int,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    if not status:
        return {"healthy": False, "reason": "missing status", "age_seconds": None, "error_code": "NO_STATUS"}
    if not bool(status.get("enabled")):
        return {"healthy": True, "reason": "disabled", "age_seconds": None, "error_code": None}
    completed = status.get("last_completed_at")
    age = None if completed is None else int((int(time.time()) if now is None else now) - int(completed))
    if bool(status.get("running")):
        return {"healthy": True, "reason": "running", "age_seconds": age, "error_code": None}
    if not bool(status.get("ok")):
        return {"healthy": False, "reason": str(status.get("last_error") or "last run failed"), "age_seconds": age, "error_code": "LAST_RUN_FAILED"}
    if age is None or age > max_age_seconds:
        return {"healthy": False, "reason": "status stale", "age_seconds": age, "error_code": "STALE_STATUS"}
    return {"healthy": True, "reason": "ok", "age_seconds": age, "error_code": None}


def _request_state(state: dict[str, Any], request_key: str) -> dict[str, Any]:
    requests = state.setdefault("requests", {})
    if not isinstance(requests, dict):
        state["requests"] = requests = {}
    row = requests.setdefault(request_key, {})
    if not isinstance(row, dict):
        requests[request_key] = row = {}
    return row


def failed_guids_for(state: dict[str, Any], request_key: str) -> set[str]:
    row = _request_state(state, request_key)
    failed = row.get("failed_guids")
    if not isinstance(failed, list):
        return set()
    return {str(guid) for guid in failed if str(guid).strip()}


def record_attempt(
    state: dict[str, Any], request_key: str, guid: str, *, ok: bool, now: int
) -> dict[str, Any]:
    row = _request_state(state, request_key)
    attempts = row.setdefault("attempts", [])
    if isinstance(attempts, list):
        attempts.append({"guid": guid, "ok": ok, "at": now})
    if not ok:
        failed = sorted(failed_guids_for(state, request_key) | {guid})
        row["failed_guids"] = failed
    return state
