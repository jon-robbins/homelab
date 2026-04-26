from __future__ import annotations

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

