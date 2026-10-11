from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ExclusiveSession:
    session_id: str
    name_contains: str
    priority_hashes: list[str]
    paused_hashes: list[str]
    completed_hashes: list[str]
    created_at: str
    updated_at: str


def _normalize_hashes(hashes: list[str]) -> list[str]:
    return sorted({str(value).lower() for value in hashes if value})


def _session_to_payload(session: ExclusiveSession) -> dict[str, Any]:
    return {
        "active": True,
        "session_id": session.session_id,
        "name_contains": session.name_contains,
        "priority_hashes": _normalize_hashes(session.priority_hashes),
        "paused_hashes": _normalize_hashes(session.paused_hashes),
        "completed_hashes": _normalize_hashes(session.completed_hashes),
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _payload_to_session(payload: dict[str, Any]) -> ExclusiveSession:
    return ExclusiveSession(
        session_id=str(payload["session_id"]),
        name_contains=str(payload["name_contains"]),
        priority_hashes=_normalize_hashes(list(payload.get("priority_hashes") or [])),
        paused_hashes=_normalize_hashes(list(payload.get("paused_hashes") or [])),
        completed_hashes=_normalize_hashes(list(payload.get("completed_hashes") or [])),
        created_at=str(payload["created_at"]),
        updated_at=str(payload["updated_at"]),
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    temp.write_text(encoded + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_session(path: str) -> ExclusiveSession | None:
    target = Path(path)
    if not target.exists():
        return None
    raw = target.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not payload.get("active"):
        return None
    return _payload_to_session(payload)


def write_session(path: str, session: ExclusiveSession | None) -> None:
    target = Path(path)
    if session is None:
        _atomic_write_json(target, {"active": False})
        return
    _atomic_write_json(target, _session_to_payload(session))
