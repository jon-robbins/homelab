from __future__ import annotations

from typing import Any

COMPLETE_PROGRESS = 0.999

_IDLE_RECHECK_STATES = frozenset({"checkingup", "checkingdl"})


def _torrent_has_tag(torrent: dict[str, Any], tag: str) -> bool:
    raw = torrent.get("tags")
    if raw is None:
        return False
    parts = [part.strip() for part in str(raw).split(",") if part.strip()]
    return tag in parts


def _popularity(torrent: dict[str, Any]) -> float:
    return float(torrent.get("popularity") or 0.0)


def _torrent_summary(torrent: dict[str, Any]) -> dict[str, Any]:
    return {
        "hash": torrent.get("hash"),
        "name": torrent.get("name"),
        "progress": float(torrent.get("progress") or 0),
        "state": torrent.get("state"),
        "added_on": torrent.get("added_on"),
        "upspeed": int(torrent.get("upspeed") or 0),
        "popularity": _popularity(torrent),
    }


def _is_paused_state(state: object) -> bool:
    value = str(state or "").lower()
    return value.startswith("paused") or value.startswith("stopped")


def _transfer_speed_bps(torrent: dict[str, Any], direction: str) -> int:
    key = "upspeed" if direction == "up" else "dlspeed"
    return int(torrent.get(key) or 0)


def _is_uploading(torrent: dict[str, Any]) -> bool:
    return _transfer_speed_bps(torrent, "up") > 0


def _is_batch_eligible(torrent: dict[str, Any]) -> bool:
    """Exclude torrents that cannot make download progress from batch slots."""
    state = str(torrent.get("state") or "").lower()
    if state == "missingfiles":
        return False
    if state in _IDLE_RECHECK_STATES:
        return _is_uploading(torrent) or _transfer_speed_bps(torrent, "down") > 0
    return True


def list_incomplete_freeleech_hashes(
    torrents: list[dict[str, Any]],
    *,
    tag: str,
) -> list[str]:
    return [
        str(t["hash"])
        for t in _incomplete_freeleech_torrents(torrents, tag=tag)
        if t.get("hash")
    ]


def _incomplete_freeleech_torrents(
    torrents: list[dict[str, Any]],
    *,
    tag: str,
) -> list[dict[str, Any]]:
    incomplete = [
        torrent
        for torrent in torrents
        if _torrent_has_tag(torrent, tag)
        and float(torrent.get("progress") or 0) < COMPLETE_PROGRESS
        and _is_batch_eligible(torrent)
    ]
    incomplete.sort(key=_popularity, reverse=True)
    return incomplete


def partition_freeleech_batch(
    torrents: list[dict[str, Any]],
    *,
    batch_size: int,
    tag: str,
    throttle_bps: int = 10240,
) -> dict[str, Any]:
    incomplete = _incomplete_freeleech_torrents(torrents, tag=tag)
    unlimited = incomplete[:batch_size]
    throttled = incomplete[batch_size:]
    return {
        "tag": tag,
        "batch_size": batch_size,
        "throttle_bps": throttle_bps,
        "unlimited": [_torrent_summary(t) for t in unlimited],
        "throttled": [_torrent_summary(t) for t in throttled],
        "unlimited_count": len(unlimited),
        "throttled_count": len(throttled),
    }


def plan_freeleech_batch_actions(
    torrents: list[dict[str, Any]],
    *,
    batch_size: int,
    tag: str,
    throttle_bps: int,
) -> dict[str, Any]:
    incomplete = _incomplete_freeleech_torrents(torrents, tag=tag)
    unlimited = incomplete[:batch_size]
    throttled = incomplete[batch_size:]
    unlimited_hashes = [str(t["hash"]) for t in unlimited if t.get("hash")]
    throttled_hashes = [str(t["hash"]) for t in throttled if t.get("hash")]
    resume_hashes = [
        str(t["hash"])
        for t in incomplete
        if t.get("hash") and _is_paused_state(t.get("state"))
    ]
    return {
        "tag": tag,
        "batch_size": batch_size,
        "throttle_bps": throttle_bps,
        "unlimited_hashes": unlimited_hashes,
        "throttled_hashes": throttled_hashes,
        "resume_hashes": resume_hashes,
        "pause_hashes": [],
        "resume_count": len(resume_hashes),
        "pause_count": 0,
    }


def apply_freeleech_batch(
    client: Any,
    *,
    batch_size: int,
    tag: str,
    throttle_bps: int,
) -> dict[str, Any]:
    torrents = client.torrents_info_raw(tag=tag)
    plan = plan_freeleech_batch_actions(
        torrents, batch_size=batch_size, tag=tag, throttle_bps=throttle_bps
    )
    resumed = 0
    if plan["resume_hashes"]:
        resumed = client.torrents_resume(hashes=plan["resume_hashes"])
    unlimited = plan["unlimited_hashes"]
    throttled = plan["throttled_hashes"]
    if unlimited:
        client.torrents_set_download_limit(unlimited, 0)
        client.torrents_set_upload_limit(unlimited, 0)
    if throttled:
        client.torrents_set_download_limit(throttled, throttle_bps)
        client.torrents_set_upload_limit(throttled, throttle_bps)
    return {
        **plan,
        "applied_resume_count": resumed,
        "applied_pause_count": 0,
        "applied_unlimited_count": len(unlimited),
        "applied_throttled_count": len(throttled),
    }
