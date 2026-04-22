from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass


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
        # `amount_left` can be 0 during metadata-only states (e.g. forcedMetaDL),
        # so progress is the safer completion signal.
        return self.progress >= 0.999


class QBittorrentClient:
    def __init__(self, base_url: str, username: str, password: str, timeout_seconds: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())

    def _url(self, path: str, query: dict[str, object] | None = None) -> str:
        if not query:
            return f"{self.base_url}{path}"
        return f"{self.base_url}{path}?{urllib.parse.urlencode(query, doseq=True)}"

    def login(self) -> None:
        payload = urllib.parse.urlencode({"username": self.username, "password": self.password}).encode("utf-8")
        req = urllib.request.Request(
            self._url("/api/v2/auth/login"),
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with self._opener.open(req, timeout=self.timeout_seconds) as resp:
            body = resp.read().decode("utf-8", errors="ignore").strip().lower()
        if body != "ok.":
            raise RuntimeError("qBittorrent login failed")

    def torrents_info(self) -> list[QBTorrent]:
        req = urllib.request.Request(self._url("/api/v2/torrents/info"), method="GET")
        with self._opener.open(req, timeout=self.timeout_seconds) as resp:
            payload = json.load(resp)
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
        self.login()
        body = urllib.parse.urlencode(
            {
                "hashes": "|".join(hashes),
                "deleteFiles": "true" if delete_files else "false",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self._url("/api/v2/torrents/delete"),
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with self._opener.open(req, timeout=self.timeout_seconds):
            return
