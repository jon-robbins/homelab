from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from homelab_workers.shared.qbt_events_state import ExclusiveSession, read_session, write_session

# Collapse dots/spaces/underscores/hyphens so "90 Day Fiance" matches "90.Day.Fiance".
_MATCH_SEP_RE = re.compile(r"[\s._\-]+")

ACTIVE_DL_STATES: frozenset[str] = frozenset(
    {
        "downloading",
        "forcedDL",
        "stalledDL",
        "queuedDL",
        "metaDL",
        "forcedMetaDL",
        "allocating",
        "checkingDL",
        "moving",
    }
)

DONE_STATES: frozenset[str] = frozenset(
    {
        "uploading",
        "stalledUP",
        "queuedUP",
        "forcedUP",
        "pausedUP",
        "stoppedUP",
        "checkingUP",
    }
)


class SessionActiveError(Exception):
    """Raised when starting a session while one is already active."""


class NoMatchesError(Exception):
    """Raised when no torrents match the name needle."""


class NoSessionError(Exception):
    """Raised when an operation requires an active session."""


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _torrent_hash(torrent: dict[str, Any]) -> str:
    return str(torrent.get("hash", "")).lower()


def _normalize_match_key(value: str) -> str:
    return _MATCH_SEP_RE.sub("", value.casefold())


def match_name_contains(torrents: list[dict], needle: str) -> list[dict]:
    normalized = _normalize_match_key(needle)
    if not normalized:
        return []
    return [
        torrent
        for torrent in torrents
        if normalized in _normalize_match_key(str(torrent.get("name", "")))
    ]


def is_done(torrent: dict[str, Any]) -> bool:
    progress = float(torrent.get("progress") or 0.0)
    if progress >= 1.0:
        return True
    return str(torrent.get("state", "")) in DONE_STATES


def _is_active_download(torrent: dict[str, Any]) -> bool:
    return str(torrent.get("state", "")) in ACTIVE_DL_STATES


def _remaining_priority_hashes(session: ExclusiveSession, torrents_by_hash: dict[str, dict[str, Any]]) -> list[str]:
    remaining: list[str] = []
    completed = set(session.completed_hashes)
    for hash_value in session.priority_hashes:
        if hash_value in completed:
            continue
        torrent = torrents_by_hash.get(hash_value)
        if torrent is None or is_done(torrent):
            continue
        remaining.append(hash_value)
    return remaining


def _boost_priority_hashes(qbt: Any, hashes: list[str]) -> None:
    if not hashes:
        return
    # qBittorrent 5 clears force_start when torrents/start runs — set force after resume.
    qbt.torrents_set_priority(hashes, "top")
    qbt.torrents_resume(hashes=hashes)
    qbt.torrents_set_force_start(hashes, True)


def _resume_paused(qbt: Any, session: ExclusiveSession) -> list[str]:
    if not session.paused_hashes:
        return []
    qbt.torrents_resume(hashes=session.paused_hashes)
    return list(session.paused_hashes)


def _start_payload(session: ExclusiveSession, *, priority_names: list[str], completed_immediately: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "session_id": session.session_id,
        "name_contains": session.name_contains,
        "priority_hashes": list(session.priority_hashes),
        "priority_names": priority_names,
        "paused_hashes": list(session.paused_hashes),
        "paused_count": len(session.paused_hashes),
        "priority_count": len(session.priority_hashes),
        "created_at": session.created_at,
    }
    if completed_immediately:
        payload["completed_immediately"] = True
    return payload


def start_exclusive(qbt: Any, *, name_contains: str, session_path: str) -> dict[str, Any]:
    if read_session(session_path) is not None:
        raise SessionActiveError("exclusive session already active")

    needle = name_contains.strip()
    if not needle:
        raise ValueError("name_contains must not be empty")

    torrents = qbt.torrents_info_raw()
    matches = match_name_contains(torrents, needle)
    if not matches:
        raise NoMatchesError(f"no torrents match {name_contains!r}")

    match_hashes = {_torrent_hash(torrent) for torrent in matches}
    match_by_hash = {_torrent_hash(torrent): torrent for torrent in matches}
    completed_hashes = sorted(hash_value for hash_value in match_hashes if is_done(match_by_hash[hash_value]))
    priority_hashes = sorted(match_hashes)

    paused_hashes = sorted(
        _torrent_hash(torrent)
        for torrent in torrents
        if _torrent_hash(torrent) not in match_hashes and _is_active_download(torrent)
    )

    if paused_hashes:
        qbt.torrents_pause(hashes=paused_hashes)

    active_priority = [hash_value for hash_value in priority_hashes if hash_value not in completed_hashes]
    _boost_priority_hashes(qbt, active_priority)

    now = _utc_now_iso()
    session = ExclusiveSession(
        session_id=str(uuid.uuid4()),
        name_contains=name_contains,
        priority_hashes=priority_hashes,
        paused_hashes=paused_hashes,
        completed_hashes=completed_hashes,
        created_at=now,
        updated_at=now,
    )

    priority_names = [str(match_by_hash[hash_value].get("name", "")) for hash_value in priority_hashes]
    remaining = _remaining_priority_hashes(session, match_by_hash)
    if not remaining:
        _resume_paused(qbt, session)
        write_session(session_path, None)
        return _start_payload(session, priority_names=priority_names, completed_immediately=True)

    write_session(session_path, session)
    return _start_payload(session, priority_names=priority_names)


def mark_finished(qbt: Any, *, hash_value: str, session_path: str) -> dict[str, Any]:
    session = read_session(session_path)
    if session is None:
        return {"handled": False, "action": "noop", "reason": "no_active_session", "detail": {}}

    normalized = hash_value.lower()
    if normalized not in session.priority_hashes:
        return {"handled": False, "action": "noop", "reason": "hash_not_in_priority_set", "detail": {}}

    completed_hashes = sorted(set(session.completed_hashes) | {normalized})
    torrents = qbt.torrents_info_raw()
    torrents_by_hash = {_torrent_hash(torrent): torrent for torrent in torrents}
    updated = ExclusiveSession(
        session_id=session.session_id,
        name_contains=session.name_contains,
        priority_hashes=list(session.priority_hashes),
        paused_hashes=list(session.paused_hashes),
        completed_hashes=completed_hashes,
        created_at=session.created_at,
        updated_at=_utc_now_iso(),
    )

    remaining = _remaining_priority_hashes(updated, torrents_by_hash)
    if remaining:
        write_session(session_path, updated)
        return {
            "handled": True,
            "action": "exclusive_mark_complete",
            "reason": "hash_in_priority_set",
            "detail": {
                "remaining": len(remaining),
                "completed": len(updated.completed_hashes),
            },
        }

    _resume_paused(qbt, updated)
    write_session(session_path, None)
    return {
        "handled": True,
        "action": "exclusive_session_complete",
        "reason": "hash_in_priority_set",
        "detail": {},
    }


def reenforce_exclusive(qbt: Any, *, session_path: str) -> dict[str, Any]:
    session = read_session(session_path)
    if session is None:
        return {}

    torrents = qbt.torrents_info_raw()
    matches = match_name_contains(torrents, session.name_contains)
    match_hashes = {_torrent_hash(torrent) for torrent in matches}
    torrents_by_hash = {_torrent_hash(torrent): torrent for torrent in torrents}

    new_priority = sorted(set(session.priority_hashes) | match_hashes)
    newly_matched = sorted(set(new_priority) - set(session.priority_hashes))

    new_paused = list(session.paused_hashes)
    for torrent in torrents:
        hash_value = _torrent_hash(torrent)
        if hash_value in new_priority:
            continue
        if not _is_active_download(torrent):
            continue
        if hash_value not in new_paused:
            new_paused.append(hash_value)

    new_paused = sorted(set(new_paused))
    to_pause = sorted(set(new_paused) - set(session.paused_hashes))
    if to_pause:
        qbt.torrents_pause(hashes=to_pause)

    completed_hashes = sorted(
        hash_value
        for hash_value in new_priority
        if hash_value in session.completed_hashes
        or (hash_value in torrents_by_hash and is_done(torrents_by_hash[hash_value]))
    )
    # Re-boost all remaining priority hashes every cycle. qBT can drop force_start
    # under max_active_downloads / other reconciler churn; newly_matched alone is not enough.
    to_boost = [
        hash_value
        for hash_value in new_priority
        if hash_value not in completed_hashes and hash_value in torrents_by_hash
    ]
    _boost_priority_hashes(qbt, to_boost)

    updated = ExclusiveSession(
        session_id=session.session_id,
        name_contains=session.name_contains,
        priority_hashes=new_priority,
        paused_hashes=new_paused,
        completed_hashes=completed_hashes,
        created_at=session.created_at,
        updated_at=_utc_now_iso(),
    )

    remaining = _remaining_priority_hashes(updated, torrents_by_hash)
    if not remaining:
        _resume_paused(qbt, updated)
        write_session(session_path, None)
        return {"completed_immediately": True}

    write_session(session_path, updated)
    return {
        "priority_count": len(updated.priority_hashes),
        "paused_count": len(updated.paused_hashes),
        "added_priority": newly_matched,
        "added_paused": to_pause,
    }


def cancel_exclusive(qbt: Any, *, session_path: str) -> dict[str, Any]:
    session = read_session(session_path)
    if session is None:
        raise NoSessionError("no active exclusive session")

    resumed_hashes = _resume_paused(qbt, session)
    write_session(session_path, None)
    return {
        "ok": True,
        "resumed_count": len(resumed_hashes),
        "resumed_hashes": resumed_hashes,
        "session_id": session.session_id,
    }


def status_payload(session_path: str) -> dict[str, Any]:
    session = read_session(session_path)
    if session is None:
        return {"ok": True, "active": False, "session": None}

    remaining_hashes = [
        hash_value
        for hash_value in session.priority_hashes
        if hash_value not in session.completed_hashes
    ]
    return {
        "ok": True,
        "active": True,
        "session": {
            "session_id": session.session_id,
            "name_contains": session.name_contains,
            "priority_count": len(session.priority_hashes),
            "completed_count": len(session.completed_hashes),
            "remaining_count": len(remaining_hashes),
            "paused_count": len(session.paused_hashes),
            "completed_hashes": list(session.completed_hashes),
            "remaining_hashes": remaining_hashes,
            "paused_hashes": list(session.paused_hashes),
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        },
    }
