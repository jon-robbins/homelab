from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .models import RouterSessionState


class RouterStateStore:
    """Tiny JSON-file state store for pending option selections."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def _read_all(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8").strip()
            if not raw:
                return {}
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {}
            return data
        except (OSError, json.JSONDecodeError, ValueError):
            return {}

    def _write_all(self, data: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._path)

    def get(self, session_key: str) -> RouterSessionState | None:
        data = self._read_all()
        row = data.get(session_key)
        if not isinstance(row, dict):
            return None
        try:
            state = RouterSessionState.model_validate(row)
        except Exception:  # noqa: BLE001
            return None
        now = int(time.time() * 1000)
        if state.expires_at_ms <= now:
            self.clear(session_key)
            return None
        return state

    def set(self, state: RouterSessionState) -> None:
        data = self._read_all()
        data[state.session_key] = state.model_dump()
        self._write_all(data)

    def clear(self, session_key: str) -> None:
        data = self._read_all()
        if session_key in data:
            data.pop(session_key, None)
            self._write_all(data)
