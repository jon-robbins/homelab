from __future__ import annotations

import time
from typing import Any

COMPLETE_PROGRESS = 0.999
DEFAULT_TRACKER_TAGS = frozenset({"fictional-seed", "fictional-tracker", "freeleech"})
RECENT_SEED_TAG = "fictional-seed"
GUARDRAIL_OVERFLOW = "GUARDRAIL_OVERFLOW"


def _is_paused_state(state: object) -> bool:
    value = str(state or "").lower()
    return value.startswith("paused") or value.startswith("stopped")


def _torrent_has_any_tag(torrent: dict[str, Any], tags: frozenset[str]) -> bool:
    raw = torrent.get("tags")
    if raw is None:
        return False
    parts = {part.strip() for part in str(raw).split(",") if part.strip()}
    return bool(parts & tags)


def _torrent_size(torrent: dict[str, Any]) -> int:
    return int(torrent.get("size") or 0)


def _is_guardrailed(
    torrent: dict[str, Any],
    *,
    now: int,
    recency_hours: int,
    recent_seed_tag: str = RECENT_SEED_TAG,
) -> bool:
    if int(torrent.get("num_leechs") or 0) > 0:
        return True
    if not _torrent_has_any_tag(torrent, frozenset({recent_seed_tag})):
        return False
    cutoff = now - (recency_hours * 3600)
    return int(torrent.get("added_on") or 0) >= cutoff


def _priority_row(torrent: dict[str, Any]) -> dict[str, Any]:
    return {
        "hash": str(torrent.get("hash") or ""),
        "name": str(torrent.get("name") or ""),
        "size_bytes": _torrent_size(torrent),
        "tags": str(torrent.get("tags") or ""),
        "num_leechs": int(torrent.get("num_leechs") or 0),
    }


def plan_seed_slots(
    torrents: list[dict[str, Any]],
    *,
    now: int | None = None,
    slot_limit: int = 200,
    recency_hours: int = 48,
    tracker_tags: frozenset[str] | None = None,
    recent_seed_tag: str = RECENT_SEED_TAG,
) -> dict[str, Any]:
    ts = int(now if now is not None else time.time())
    tags = tracker_tags or DEFAULT_TRACKER_TAGS

    candidates = [
        torrent
        for torrent in torrents
        if float(torrent.get("progress") or 0) >= COMPLETE_PROGRESS
        and _torrent_has_any_tag(torrent, tags)
        and torrent.get("hash")
    ]

    if not candidates:
        return {
            "slot_limit": slot_limit,
            "recency_hours": recency_hours,
            "candidate_count": 0,
            "priority_count": 0,
            "guardrail_count": 0,
            "priority": [],
            "priority_sample": [],
            "resume_hashes": [],
            "pause_hashes": [],
            "evicted": [],
            "warnings": [],
        }

    guardrailed = {
        str(torrent["hash"]): torrent
        for torrent in candidates
        if _is_guardrailed(
            torrent, now=ts, recency_hours=recency_hours, recent_seed_tag=recent_seed_tag
        )
    }

    sorted_candidates = sorted(
        candidates,
        key=lambda row: (_torrent_size(row), int(row.get("added_on") or 0)),
        reverse=True,
    )

    priority_by_hash: dict[str, dict[str, Any]] = {
        str(torrent["hash"]): torrent for torrent in sorted_candidates[:slot_limit]
    }

    evicted: list[dict[str, Any]] = []
    warnings: list[str] = []

    for hash_value, torrent in guardrailed.items():
        if hash_value in priority_by_hash:
            continue
        priority_by_hash[hash_value] = torrent
        while len(priority_by_hash) > slot_limit:
            evictable = [
                row for key, row in priority_by_hash.items() if key not in guardrailed
            ]
            if not evictable:
                warnings.append(GUARDRAIL_OVERFLOW)
                break
            victim = min(evictable, key=lambda row: (_torrent_size(row), -int(row.get("added_on") or 0)))
            victim_hash = str(victim["hash"])
            evicted.append(
                {
                    "hash": victim_hash,
                    "name": str(victim.get("name") or ""),
                    "size_bytes": _torrent_size(victim),
                    "reason": "size_demoted",
                }
            )
            del priority_by_hash[victim_hash]

    priority_hashes = set(priority_by_hash.keys())

    resume_hashes = [
        str(torrent["hash"])
        for torrent in priority_by_hash.values()
        if _is_paused_state(torrent.get("state"))
    ]
    pause_hashes = [
        str(torrent["hash"])
        for torrent in candidates
        if str(torrent["hash"]) not in priority_hashes
        and int(torrent.get("num_leechs") or 0) == 0
        and not _is_paused_state(torrent.get("state"))
    ]

    priority_rows = sorted(
        (_priority_row(torrent) for torrent in priority_by_hash.values()),
        key=lambda row: row["size_bytes"],
        reverse=True,
    )

    return {
        "slot_limit": slot_limit,
        "recency_hours": recency_hours,
        "candidate_count": len(candidates),
        "priority_count": len(priority_by_hash),
        "guardrail_count": len(guardrailed),
        "priority": priority_rows,
        "priority_sample": priority_rows[:10],
        "resume_hashes": resume_hashes,
        "pause_hashes": pause_hashes,
        "evicted": evicted,
        "warnings": warnings,
    }


def apply_seed_slots(client: Any, plan: dict[str, Any]) -> dict[str, Any]:
    resumed = 0
    paused = 0
    if plan.get("resume_hashes"):
        resumed = client.torrents_resume(hashes=list(plan["resume_hashes"]))
    if plan.get("pause_hashes"):
        paused = client.torrents_pause(hashes=list(plan["pause_hashes"]))
    return {
        **plan,
        "applied_resume_count": resumed,
        "applied_pause_count": paused,
    }
