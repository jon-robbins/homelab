"""Session state store for pending router selections.

Persists ``RouterSessionState`` rows to a tiny JSON file so that an
``options`` round-trip (e.g. ``indexer_search`` returning ranked options)
can be followed up later by a selection (e.g. ``"first option"``) and
resolved to the matching ``RouterPendingOption``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ..models.router import RouterPendingOption, RouterSessionState
from .intent import SelectionChoice  # noqa: F401  — re-export-friendly


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


_SESSION_OPTION_SOURCE_ACTIONS: frozenset[str] = frozenset(
    {"download_options_tv", "download_options_movie", "indexer_search"}
)


def selection_to_action_from_session(
    state: RouterSessionState, selected: RouterPendingOption
) -> dict[str, Any] | None:
    """Delegate to the source action's handler ``selection_to_grab``."""
    from ..actions import registry

    if not registry.has(state.source_action):
        return None
    return registry.get(state.source_action).selection_to_grab(state, selected)


def maybe_persist_pending_options(
    *,
    store: RouterStateStore,
    session_key: str | None,
    action_payload: dict[str, Any],
    tool_result: dict[str, Any],
    ttl_s: int,
) -> int:
    """If the result is a successful options list, persist a session row.

    Returns the number of options persisted (0 if not persisted).
    """
    from ..services.torrent_naming import extract_season_number
    from .intent import build_pending_options

    action_name = str(action_payload.get("action") or "")
    if not session_key:
        return 0
    if action_name not in _SESSION_OPTION_SOURCE_ACTIONS:
        return 0
    if tool_result.get("ok") is not True:
        return 0
    options = tool_result.get("options") or []
    if not options:
        return 0

    pending = build_pending_options(action_name, tool_result)
    if not pending:
        return 0

    inferred_season = action_payload.get("season")
    if not isinstance(inferred_season, int):
        inferred_season = extract_season_number(str(action_payload.get("query") or ""))
    inferred_media_type = action_payload.get("type")
    if inferred_media_type is None and action_name == "indexer_search":
        inferred_media_type = (
            "tv"
            if extract_season_number(str(action_payload.get("query") or "")) is not None
            else None
        )

    now_ms = int(time.time() * 1000)
    expires_ms = now_ms + max(30, int(ttl_s)) * 1000
    store.set(
        RouterSessionState(
            session_key=session_key,
            created_at_ms=now_ms,
            expires_at_ms=expires_ms,
            source_action=action_name,  # type: ignore[arg-type]
            query=str(action_payload.get("query") or ""),
            media_type=inferred_media_type,
            season=inferred_season,
            options=pending,
        )
    )
    return len(pending)


__all__ = [
    "RouterStateStore",
    "selection_to_action_from_session",
    "maybe_persist_pending_options",
]
