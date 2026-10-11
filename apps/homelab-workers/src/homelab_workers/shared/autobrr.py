from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from homelab_workers.shared.qbittorrent import find_hash_in_torrents

@dataclass(frozen=True)
class AutobrrClient:
    base_url: str
    api_token: str
    timeout_seconds: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    def __enter__(self) -> AutobrrClient:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None

    def _headers(self) -> dict[str, str]:
        return {"X-API-Token": self.api_token}

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        with httpx.Client(base_url=self.base_url, timeout=float(self.timeout_seconds)) as client:
            response = client.get(path, headers=self._headers(), params=params or {})
            response.raise_for_status()
            return response.json()

    def _put(self, path: str, *, json_body: dict[str, Any]) -> Any:
        with httpx.Client(base_url=self.base_url, timeout=float(self.timeout_seconds)) as client:
            response = client.put(path, headers=self._headers(), json=json_body)
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()

    def _find_filter_id(self, name: str) -> int:
        for row in self.filters():
            if str(row.get("name") or "") == name:
                return int(row["id"])
        raise ValueError(f"filter not found: {name}")

    def set_filter_enabled(self, filter_name: str, *, enabled: bool) -> dict[str, Any]:
        filter_id = self._find_filter_id(filter_name)
        self._put(f"/api/filters/{filter_id}/enabled", json_body={"enabled": enabled})
        return {"id": filter_id, "name": filter_name, "enabled": enabled}

    def cleanup_backup(
        self,
        filter_names: list[str],
        release_limit: int = 1000,
    ) -> dict[str, Any]:
        wanted = set(filter_names)
        releases = [
            release
            for release in self.releases(limit=release_limit)
            if not wanted or str(release.get("filter") or "") in wanted
        ]
        return {
            "available": True,
            "filters": self.filters(),
            "filter_names": filter_names,
            "releases": releases,
            "history_deletion_supported": False,
        }

    def disable_filters(self, filter_names: list[str]) -> list[dict[str, Any]]:
        return [self.set_filter_enabled(name, enabled=False) for name in filter_names]

    def restart_irc_network(self, network_name: str) -> dict[str, Any]:
        network_id: int | None = None
        for row in self.irc_networks():
            if str(row.get("name") or "") == network_name:
                network_id = int(row["id"])
                break
        if network_id is None:
            raise ValueError(f"irc network not found: {network_name}")
        with httpx.Client(base_url=self.base_url, timeout=float(self.timeout_seconds)) as client:
            response = client.get(
                f"/api/irc/network/{network_id}/restart",
                headers=self._headers(),
            )
            response.raise_for_status()
        return {"id": network_id, "name": network_name, "restarted": True}

    def filters(self) -> list[dict[str, Any]]:
        payload = self._get("/api/filters")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def irc_networks(self) -> list[dict[str, Any]]:
        payload = self._get("/api/irc")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def releases(self, *, limit: int = 100) -> list[dict[str, Any]]:
        payload = self._get("/api/release", params={"limit": limit})
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []


def parse_release_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def primary_push_status(action_status: Any) -> str | None:
    if not isinstance(action_status, list) or not action_status:
        return None
    first = action_status[0]
    if isinstance(first, dict):
        status = first.get("status")
        return str(status) if status else None
    return None


def release_matches_filter(release: dict[str, Any], filter_name: str | None) -> bool:
    if not filter_name:
        return True
    return str(release.get("filter") or "") == filter_name


def release_in_window(release: dict[str, Any], *, since: datetime) -> bool:
    ts = parse_release_timestamp(str(release.get("timestamp") or ""))
    if ts is None:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= since


def summarize_releases(
    releases: list[dict[str, Any]],
    *,
    since: datetime,
    filter_name: str | None = None,
) -> dict[str, int]:
    counts = {
        "total": 0,
        "filter_approved": 0,
        "filter_rejected": 0,
        "push_approved": 0,
        "push_rejected": 0,
        "push_error": 0,
    }
    for release in releases:
        if not release_in_window(release, since=since):
            continue
        if not release_matches_filter(release, filter_name):
            continue
        counts["total"] += 1
        filter_status = str(release.get("filter_status") or "")
        if filter_status == "FILTER_APPROVED":
            counts["filter_approved"] += 1
        else:
            counts["filter_rejected"] += 1
        push_status = primary_push_status(release.get("action_status"))
        if push_status == "PUSH_APPROVED":
            counts["push_approved"] += 1
        elif push_status == "PUSH_ERROR":
            counts["push_error"] += 1
        elif push_status in {"PUSH_REJECTED", "PUSH_PENDING"}:
            counts["push_rejected"] += 1
    return counts


def quota_for_filter(
    releases: list[dict[str, Any]],
    *,
    filter_name: str,
    since: datetime,
    max_downloads: int,
) -> dict[str, Any]:
    used = summarize_releases(releases, since=since, filter_name=filter_name)["push_approved"]
    remaining = max(max_downloads - used, 0)
    return {
        "filter": filter_name,
        "max_downloads": max_downloads,
        "used_today": used,
        "remaining_today": remaining,
        "at_quota": used >= max_downloads,
    }


def extract_rejection_messages(release: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    for item in release.get("rejections") or []:
        if isinstance(item, dict):
            msg = item.get("message") or item.get("reason")
            if msg:
                messages.append(str(msg))
        elif isinstance(item, str) and item:
            messages.append(item)
    for action in release.get("action_status") or []:
        if not isinstance(action, dict):
            continue
        for item in action.get("rejections") or []:
            if isinstance(item, dict):
                msg = item.get("message") or item.get("reason")
                if msg:
                    messages.append(str(msg))
            elif isinstance(item, str) and item:
                messages.append(item)
    return messages


def release_normalized_hash(release: dict[str, Any]) -> str | None:
    value = release.get("normalized_hash")
    if not value:
        return None
    text = str(value).strip().lower()
    return text or None


def normalize_torrent_name(name: str) -> str:
    text = name.lower()
    text = re.sub(r"\.(mkv|mp4|avi|m2ts|ts|torrent)$", "", text)
    text = re.sub(r"season\s*\d+", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def release_found_in_qbt(release: dict[str, Any], qbt_torrents: list[dict[str, Any]]) -> bool:
    info_hash = release_normalized_hash(release)
    if info_hash:
        if find_hash_in_torrents(qbt_torrents, info_hash):
            return True
        for torrent in qbt_torrents:
            for key in ("hash", "infohash_v1", "infohash_v2"):
                value = str(torrent.get(key) or "").strip().lower()
                if not value:
                    continue
                if value.startswith(info_hash) or info_hash.startswith(value):
                    return True

    release_name = normalize_torrent_name(str(release.get("name") or ""))
    if not release_name:
        return False
    for torrent in qbt_torrents:
        qbt_name = normalize_torrent_name(str(torrent.get("name") or ""))
        if release_name == qbt_name:
            return True
    return False


def compact_release(release: dict[str, Any]) -> dict[str, Any]:
    size_bytes = int(release.get("size") or 0)
    out: dict[str, Any] = {
        "name": release.get("name"),
        "filter": release.get("filter"),
        "filter_status": release.get("filter_status"),
        "push_status": primary_push_status(release.get("action_status")),
        "size_gb": round(size_bytes / (1024**3), 2) if size_bytes else 0.0,
        "timestamp": release.get("timestamp"),
        "category": release.get("category"),
    }
    rejections = extract_rejection_messages(release)
    if rejections:
        out["rejections"] = rejections
    hash_value = release_normalized_hash(release)
    if hash_value:
        out["normalized_hash"] = hash_value
    return out


def compact_irc_network(network: dict[str, Any]) -> dict[str, Any]:
    channels: list[dict[str, Any]] = []
    for ch in network.get("channels") or []:
        if not isinstance(ch, dict):
            continue
        channels.append(
            {
                "name": ch.get("name"),
                "monitoring": bool(ch.get("monitoring")),
                "last_announce": ch.get("last_announce"),
            }
        )
    out: dict[str, Any] = {
        "name": network.get("name"),
        "healthy": bool(network.get("healthy")),
        "connected": bool(network.get("connected")),
        "connection_errors": list(network.get("connection_errors") or []),
        "channels": channels,
    }
    auth = network.get("auth")
    if isinstance(auth, dict):
        mechanism = auth.get("mechanism")
        if mechanism is not None:
            out["auth_mechanism"] = mechanism
    return out


def start_of_today(tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def since_hours(hours: int, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    return (now - timedelta(hours=hours)).astimezone(timezone.utc)


def verify_recent_pushes_in_qbt(
    releases: list[dict[str, Any]],
    *,
    qbt_torrents: list[dict[str, Any]],
    recent_limit: int = 10,
) -> dict[str, Any]:
    sorted_releases = sorted(
        releases,
        key=lambda item: parse_release_timestamp(str(item.get("timestamp") or ""))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    missing: list[dict[str, Any]] = []
    checked = 0
    for release in sorted_releases[:recent_limit]:
        if primary_push_status(release.get("action_status")) != "PUSH_APPROVED":
            continue
        info_hash = release_normalized_hash(release)
        if not info_hash:
            continue
        checked += 1
        if not release_found_in_qbt(release, qbt_torrents):
            missing.append(
                {
                    "name": release.get("name"),
                    "normalized_hash": info_hash,
                    "timestamp": release.get("timestamp"),
                }
            )
    return {"checked": checked, "missing_in_qbt": missing}


def build_operational_summary(
    *,
    filters: list[dict[str, Any]],
    irc_networks: list[dict[str, Any]],
    releases: list[dict[str, Any]],
    tz_name: str,
    recent_limit: int = 10,
) -> dict[str, Any]:
    today_start = start_of_today(tz_name)
    today_stats = summarize_releases(releases, since=today_start)

    quota_rows: list[dict[str, Any]] = []
    for filt in filters:
        name = str(filt.get("name") or "")
        max_downloads = int(filt.get("max_downloads") or 0)
        unit = str(filt.get("max_downloads_unit") or "")
        if not name or max_downloads <= 0 or unit.upper() != "DAY":
            continue
        quota_rows.append(
            quota_for_filter(
                releases,
                filter_name=name,
                since=today_start,
                max_downloads=max_downloads,
            )
        )

    sorted_releases = sorted(
        releases,
        key=lambda item: parse_release_timestamp(str(item.get("timestamp") or ""))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    return {
        "irc": [compact_irc_network(net) for net in irc_networks],
        "filters": [
            {
                "name": filt.get("name"),
                "enabled": bool(filt.get("enabled")),
                "max_downloads": filt.get("max_downloads"),
                "max_downloads_unit": filt.get("max_downloads_unit"),
            }
            for filt in filters
        ],
        "today": {
            **today_stats,
            "quota_by_filter": quota_rows,
            "timezone": tz_name,
        },
        "recent": [compact_release(item) for item in sorted_releases[:recent_limit]],
    }
