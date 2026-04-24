from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin

import httpx

from ..router_runtime_helpers import (
    _extract_season_number,
    _is_multi_season_pack,
    _query_matches_torrent_name,
    _season_path_matches,
)


def find_completed_download_name(
    http: httpx.Client,
    s: Any,
    query: str,
    season: int | None,
) -> str | None:
    if not s.qbittorrent_configured:
        return None
    try:
        cookies = qb_login(http, s)
        if cookies is None:
            return None
        res = http.get(
            urljoin(f"{s.qbittorrent_base}/", "api/v2/torrents/info"),
            params={"filter": "completed"},
            cookies=cookies,
            timeout=s.upstream_timeout_s,
        )
        res.raise_for_status()
        payload = res.json()
        if not isinstance(payload, list):
            return None
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if name and _query_matches_torrent_name(query=query, torrent_name=name, season=season):
                return name
    except Exception:  # noqa: BLE001
        return None
    return None


def qb_file_id(file_row: dict[str, Any]) -> int | None:
    for key in ("index", "id"):
        value = file_row.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def qb_file_priority(file_row: dict[str, Any]) -> int:
    value = file_row.get("priority")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def qb_login(http: httpx.Client, s: Any) -> httpx.Cookies | None:
    if not s.qbittorrent_configured:
        return None
    auth = http.post(
        urljoin(f"{s.qbittorrent_base}/", "api/v2/auth/login"),
        data={
            "username": s.qbittorrent_username,
            "password": s.qbittorrent_password,
        },
        timeout=s.upstream_timeout_s,
    )
    auth.raise_for_status()
    return auth.cookies


def season_only_selection_after_grab(
    http: httpx.Client,
    s: Any,
    release: dict[str, Any],
    season: int,
) -> dict[str, Any] | None:
    try:
        cookies = qb_login(http, s)
        if cookies is None:
            return None
        info_hash = str(release.get("infoHash") or "").strip().lower()
        if not info_hash:
            return None
        wanted_ids: list[int] = []
        other_season_ids: list[int] = []
        deadline = time.monotonic() + 20.0
        while time.monotonic() <= deadline:
            files_resp = http.get(
                urljoin(f"{s.qbittorrent_base}/", "api/v2/torrents/files"),
                params={"hash": info_hash},
                cookies=cookies,
                timeout=s.upstream_timeout_s,
            )
            files_resp.raise_for_status()
            files = files_resp.json()
            if not isinstance(files, list):
                return None
            wanted_ids = []
            other_season_ids = []
            for row in files:
                if not isinstance(row, dict):
                    continue
                fid = qb_file_id(row)
                if fid is None:
                    continue
                file_season = _extract_season_number(str(row.get("name") or ""))
                if file_season is None:
                    continue
                if file_season == season:
                    wanted_ids.append(fid)
                else:
                    other_season_ids.append(fid)
            if wanted_ids:
                break
            time.sleep(1.0)
        if not wanted_ids:
            return {
                "status": "no_season_files_found",
                "season": season,
                "torrent_hash": info_hash,
            }
        if other_season_ids:
            http.post(
                urljoin(f"{s.qbittorrent_base}/", "api/v2/torrents/filePrio"),
                data={
                    "hash": info_hash,
                    "id": "|".join(str(x) for x in other_season_ids),
                    "priority": "0",
                },
                cookies=cookies,
                timeout=s.upstream_timeout_s,
            ).raise_for_status()
        http.post(
            urljoin(f"{s.qbittorrent_base}/", "api/v2/torrents/filePrio"),
            data={
                "hash": info_hash,
                "id": "|".join(str(x) for x in wanted_ids),
                "priority": "1",
            },
            cookies=cookies,
            timeout=s.upstream_timeout_s,
        ).raise_for_status()
        return {
            "status": "season_only_applied",
            "season": season,
            "torrent_hash": info_hash,
            "enabled_file_count": len(wanted_ids),
            "disabled_other_season_file_count": len(other_season_ids),
        }
    except Exception:  # noqa: BLE001
        return None


def try_enable_requested_season_in_existing_torrent(
    http: httpx.Client,
    s: Any,
    query: str,
    season: int,
) -> dict[str, Any] | None:
    if not s.qbittorrent_configured:
        return None
    try:
        cookies = qb_login(http, s)
        if cookies is None:
            return None
        torrents_resp = http.get(
            urljoin(f"{s.qbittorrent_base}/", "api/v2/torrents/info"),
            params={"filter": "all"},
            cookies=cookies,
            timeout=s.upstream_timeout_s,
        )
        torrents_resp.raise_for_status()
        torrents = torrents_resp.json()
        if not isinstance(torrents, list):
            return None

        for t in torrents:
            if not isinstance(t, dict):
                continue
            torrent_name = str(t.get("name") or "")
            torrent_hash = str(t.get("hash") or "")
            if not torrent_name or not torrent_hash:
                continue
            if not _is_multi_season_pack(torrent_name):
                continue
            if not _query_matches_torrent_name(query=query, torrent_name=torrent_name, season=None):
                continue
            files_resp = http.get(
                urljoin(f"{s.qbittorrent_base}/", "api/v2/torrents/files"),
                params={"hash": torrent_hash},
                cookies=cookies,
                timeout=s.upstream_timeout_s,
            )
            files_resp.raise_for_status()
            files = files_resp.json()
            if not isinstance(files, list):
                continue
            season_files = [
                row
                for row in files
                if isinstance(row, dict)
                and _season_path_matches(str(row.get("name") or ""), season)
            ]
            if not season_files:
                continue
            pending_ids: list[int] = []
            completed_count = 0
            for row in season_files:
                fid = qb_file_id(row)
                if fid is None:
                    continue
                prio = qb_file_priority(row)
                progress = float(row.get("progress") or 0.0)
                if prio == 0:
                    pending_ids.append(fid)
                elif progress >= 0.999:
                    completed_count += 1
            if pending_ids:
                http.post(
                    urljoin(f"{s.qbittorrent_base}/", "api/v2/torrents/filePrio"),
                    data={
                        "hash": torrent_hash,
                        "id": "|".join(str(x) for x in pending_ids),
                        "priority": "1",
                    },
                    cookies=cookies,
                    timeout=s.upstream_timeout_s,
                ).raise_for_status()
                http.post(
                    urljoin(f"{s.qbittorrent_base}/", "api/v2/torrents/resume"),
                    data={"hashes": torrent_hash},
                    cookies=cookies,
                    timeout=s.upstream_timeout_s,
                ).raise_for_status()
                return {
                    "status": "enabled",
                    "torrent_name": torrent_name,
                    "torrent_hash": torrent_hash,
                    "season": season,
                    "enabled_file_count": len(pending_ids),
                }
            if completed_count == len(season_files):
                return {
                    "status": "already_downloaded",
                    "torrent_name": torrent_name,
                    "torrent_hash": torrent_hash,
                    "season": season,
                    "season_file_count": len(season_files),
                }
            return {
                "status": "already_selected",
                "torrent_name": torrent_name,
                "torrent_hash": torrent_hash,
                "season": season,
                "season_file_count": len(season_files),
            }
    except Exception:  # noqa: BLE001
        return None
    return None


def completed_download_match_for_action(
    http: httpx.Client, s: Any, action_payload: dict[str, Any]
) -> str | None:
    action = str(action_payload.get("action") or "")
    if action == "download_options_tv":
        query = str(action_payload.get("query") or "").strip()
        season = action_payload.get("season")
        return find_completed_download_name(
            http=http,
            s=s,
            query=query,
            season=season if isinstance(season, int) else None,
        )
    if action == "download_options_movie":
        query = str(action_payload.get("query") or "").strip()
        return find_completed_download_name(http=http, s=s, query=query, season=None)
    return None
