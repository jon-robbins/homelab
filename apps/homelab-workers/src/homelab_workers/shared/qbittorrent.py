from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class QBTorrent:
    torrent_hash: str
    name: str
    category: str
    added_on: int
    progress: float
    dlspeed_bps: float
    average_download_bps: float
    state: str
    num_seeds: int
    amount_left: int

    @property
    def is_complete(self) -> bool:
        return self.progress >= 0.999


class QBittorrentClient:
    def __init__(self, base_url: str, username: str, password: str, timeout_seconds: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout_seconds = timeout_seconds
        self._client = httpx.Client(base_url=self.base_url, timeout=float(timeout_seconds))
        self._login()

    def _login(self) -> None:
        resp = self._client.post(
            "/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
        )
        body = resp.text.strip().lower()
        if body != "ok.":
            raise RuntimeError("qBittorrent login failed")

    def login(self) -> None:
        self._login()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> QBittorrentClient:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def torrents_info(self) -> list[QBTorrent]:
        resp = self._client.get("/api/v2/torrents/info")
        resp.raise_for_status()
        payload: Any = resp.json()
        if not isinstance(payload, list):
            return []
        torrents: list[QBTorrent] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            torrent_hash = str(raw.get("hash", "")).lower()
            if not torrent_hash:
                continue
            downloaded = int(raw.get("downloaded") or 0)
            time_active = int(raw.get("time_active") or 0)
            avg_bps = float(downloaded / time_active) if time_active > 0 else float(raw.get("dlspeed") or 0.0)
            torrents.append(
                QBTorrent(
                    torrent_hash=torrent_hash,
                    name=str(raw.get("name", "")),
                    category=str(raw.get("category") or ""),
                    added_on=int(raw.get("added_on") or 0),
                    progress=float(raw.get("progress") or 0.0),
                    dlspeed_bps=float(raw.get("dlspeed") or 0.0),
                    average_download_bps=avg_bps,
                    state=str(raw.get("state") or ""),
                    num_seeds=int(raw.get("num_seeds") or 0),
                    amount_left=int(raw.get("amount_left") or 0),
                )
            )
        return torrents

    def torrents_delete_hashes(self, hashes: list[str], *, delete_files: bool = False) -> None:
        if not hashes:
            return
        self._login()
        resp = self._client.post(
            "/api/v2/torrents/delete",
            data={
                "hashes": "|".join(hashes),
                "deleteFiles": "true" if delete_files else "false",
            },
        )
        resp.raise_for_status()

    def transfer_info(self) -> dict[str, Any]:
        resp = self._client.get("/api/v2/transfer/info")
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else {}

    def app_preferences(self) -> dict[str, Any]:
        resp = self._client.get("/api/v2/app/preferences")
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else {}

    def torrents_info_raw(
        self,
        *,
        tag: str | None = None,
        category: str | None = None,
        filter_name: str | None = None,
        hashes: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if hashes:
            params["hashes"] = hashes
        if tag:
            params["tag"] = tag
        if category:
            params["category"] = category
        if filter_name:
            params["filter"] = filter_name
        resp = self._client.get("/api/v2/torrents/info", params=params or None)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    def torrent_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        resp = self._client.get(
            "/api/v2/torrents/trackers",
            params={"hash": torrent_hash},
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    def torrent_properties(self, torrent_hash: str) -> dict[str, Any]:
        resp = self._client.get(
            "/api/v2/torrents/properties",
            params={"hash": torrent_hash},
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else {}

    def torrent_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        resp = self._client.get(
            "/api/v2/torrents/files",
            params={"hash": torrent_hash},
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    def torrent_export(self, torrent_hash: str) -> bytes | None:
        resp = self._client.get(
            "/api/v2/torrents/export",
            params={"hash": torrent_hash},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return bytes(resp.content)

    def torrent_peers(self, torrent_hash: str) -> dict[str, Any]:
        resp = self._client.get(
            "/api/v2/sync/torrentPeers",
            params={"hash": torrent_hash},
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else {}

    def _resolve_hashes(self, *, hashes: list[str] | None, tag: str | None) -> list[str]:
        if hashes:
            return [h.strip().lower() for h in hashes if h.strip()]
        if tag:
            return [
                str(torrent.get("hash")).lower()
                for torrent in self.torrents_info_raw(tag=tag)
                if torrent.get("hash")
            ]
        raise ValueError("hashes or tag required")

    def _torrents_action(self, endpoint: str, hashes: list[str]) -> None:
        if not hashes:
            return
        self._login()
        resp = self._client.post(
            f"/api/v2/torrents/{endpoint}",
            data={"hashes": "|".join(hashes)},
        )
        resp.raise_for_status()

    def torrents_pause(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int:
        targets = self._resolve_hashes(hashes=hashes, tag=tag)
        self._torrents_action("stop", targets)
        return len(targets)

    def torrents_resume(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int:
        targets = self._resolve_hashes(hashes=hashes, tag=tag)
        self._torrents_action("start", targets)
        return len(targets)

    def set_app_preferences(self, prefs: dict[str, Any]) -> None:
        self._login()
        resp = self._client.post(
            "/api/v2/app/setPreferences",
            data={"json": json.dumps(prefs)},
        )
        resp.raise_for_status()

    def torrents_set_location(self, hashes: list[str], location: str) -> None:
        self._login()
        resp = self._client.post(
            "/api/v2/torrents/setLocation",
            data={"hashes": "|".join(hashes), "location": location},
        )
        resp.raise_for_status()

    def torrents_recheck(self, hashes: list[str]) -> None:
        if not hashes:
            return
        self._login()
        resp = self._client.post(
            "/api/v2/torrents/recheck",
            data={"hashes": "|".join(hashes)},
        )
        resp.raise_for_status()

    def add_torrent_file(
        self,
        *,
        torrent_bytes: bytes,
        filename: str,
        save_path: str,
        category: str,
        tags: tuple[str, ...],
    ) -> None:
        self._login()
        resp = self._client.post(
            "/api/v2/torrents/add",
            data={
                "savepath": save_path,
                "category": category,
                "tags": ",".join(tags),
                "paused": "false",
            },
            files={
                "torrents": (
                    filename,
                    torrent_bytes,
                    "application/x-bittorrent",
                )
            },
        )
        resp.raise_for_status()

    def torrents_set_download_limit(self, hashes: list[str], limit: int) -> int:
        if not hashes:
            return 0
        self._login()
        resp = self._client.post(
            "/api/v2/torrents/setDownloadLimit",
            data={"hashes": "|".join(hashes), "limit": str(int(limit))},
        )
        resp.raise_for_status()
        return len(hashes)

    def torrents_set_force_start(self, hashes: list[str], enabled: bool) -> int:
        if not hashes:
            return 0
        self._login()
        resp = self._client.post(
            "/api/v2/torrents/setForceStart",
            data={
                "hashes": "|".join(hashes),
                "value": "true" if enabled else "false",
            },
        )
        resp.raise_for_status()
        return len(hashes)

    def torrents_set_priority(self, hashes: list[str], priority: str) -> int:
        if not hashes:
            return 0
        endpoints = {
            "top": "topPrio",
            "bottom": "bottomPrio",
            "increase": "increasePrio",
            "decrease": "decreasePrio",
        }
        endpoint = endpoints.get(priority)
        if endpoint is None:
            raise ValueError("priority must be one of: top, bottom, increase, decrease")
        self._login()
        resp = self._client.post(
            f"/api/v2/torrents/{endpoint}",
            data={"hashes": "|".join(hashes)},
        )
        resp.raise_for_status()
        return len(hashes)

    def torrents_add_tags(self, hashes: list[str], tags: list[str]) -> int:
        if not hashes or not tags:
            return 0
        self._login()
        resp = self._client.post(
            "/api/v2/torrents/addTags",
            data={"hashes": "|".join(hashes), "tags": ",".join(tags)},
        )
        resp.raise_for_status()
        return len(hashes)

    def torrents_set_category(self, hashes: list[str], category: str) -> int:
        if not hashes:
            return 0
        self._login()
        resp = self._client.post(
            "/api/v2/torrents/setCategory",
            data={"hashes": "|".join(hashes), "category": category},
        )
        resp.raise_for_status()
        return len(hashes)

    def torrents_set_upload_limit(self, hashes: list[str], limit: int) -> int:
        if not hashes:
            return 0
        self._login()
        resp = self._client.post(
            "/api/v2/torrents/setUploadLimit",
            data={"hashes": "|".join(hashes), "limit": str(int(limit))},
        )
        resp.raise_for_status()
        return len(hashes)

    def torrents_toggle_sequential_download(self, hashes: list[str]) -> int:
        if not hashes:
            return 0
        self._login()
        resp = self._client.post(
            "/api/v2/torrents/toggleSequentialDownload",
            data={"hashes": "|".join(hashes)},
        )
        resp.raise_for_status()
        return len(hashes)

    def torrents_toggle_first_last_piece_priority(self, hashes: list[str]) -> int:
        if not hashes:
            return 0
        self._login()
        resp = self._client.post(
            "/api/v2/torrents/toggleFirstLastPiecePrio",
            data={"hashes": "|".join(hashes)},
        )
        resp.raise_for_status()
        return len(hashes)


# --- E2E helpers (raw qBT JSON dicts from compose curl, not QBTorrent dataclass) ---

_QBT_HASH_KEYS = ("hash", "infohash_v1", "infohash_v2")
_QBT_RADARR_CATEGORIES = frozenset({"radarr", "movies-radarr", "movies"})
_QBT_SONARR_CATEGORIES = frozenset({"sonarr", "tv-sonarr"})


def _is_hex_info_hash(s: str) -> bool:
    t = s.strip().lower()
    return len(t) >= 32 and all(c in "0123456789abcdef" for c in t)


def _qbt_torrent_hash_keys(torrent: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in _QBT_HASH_KEYS:
        raw = torrent.get(key)
        if not isinstance(raw, str):
            continue
        for part in raw.replace(",", "|").split("|"):
            h = part.strip().lower()
            if h:
                out.add(h)
    return out


def parse_qbt_hashes(torrents: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for t in torrents:
        out.update(_qbt_torrent_hash_keys(t))
    return out


def find_hash_in_torrents(torrents: list[dict[str, Any]], needle: str) -> bool:
    n = needle.strip().lower()
    if not n:
        return False
    for t in torrents:
        if n in _qbt_torrent_hash_keys(t):
            return True
    return False


def sample_qbt_hash_prefixes(
    torrents: list[dict[str, Any]], *, limit: int = 6, category: str | None = None
) -> list[str]:
    out: list[str] = []
    cat_l = category.strip().lower() if category else ""
    for t in torrents:
        if cat_l and (t.get("category") or "").strip().lower() != cat_l:
            continue
        for h in sorted(_qbt_torrent_hash_keys(t)):
            prefix = h[:8] if len(h) >= 8 else h
            if prefix and prefix not in out:
                out.append(prefix)
            if len(out) >= limit:
                return out
    return out


def first_qbt_info_hash(blob: str) -> str | None:
    s = (blob or "").strip().lower()
    if not s:
        return None
    for part in s.replace(",", "|").split("|"):
        p = part.strip().lower()
        if len(p) >= 32 and all(c in "0123456789abcdef" for c in p):
            return p
    return None


def _merge_qbt_hash_blobs(*blobs: str) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for blob in blobs:
        for part in (blob or "").replace(",", "|").split("|"):
            h = part.strip().lower()
            if h and h not in seen:
                seen.add(h)
                out.append(h)
    return "|".join(out)
