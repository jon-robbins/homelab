"""Bazarr HTTP helpers for integration tests (stdlib urllib only).

Used by ``tests/integration/bazarr_e2e_subtitle_probe.py``.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _get(base: str, path: str, key: str) -> object:
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"X-API-KEY": key})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def _patch(base: str, path_qs: str, key: str) -> None:
    url = base.rstrip("/") + path_qs
    req = urllib.request.Request(url, headers={"X-API-KEY": key}, method="PATCH", data=b"")
    with urllib.request.urlopen(req, timeout=300) as resp:
        resp.read()


def post_system_task(base: str, key: str, taskid: str) -> None:
    url = base.rstrip("/") + f"/api/system/tasks?taskid={urllib.parse.quote(taskid, safe='')}"
    req = urllib.request.Request(url, headers={"X-API-KEY": key}, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def load_bazarr_api_key(repo_root: Path) -> str:
    k = (os.environ.get("BAZARR_API_KEY") or "").strip()
    if k:
        return k
    p = repo_root / "data" / "bazarr" / "config" / "config.yaml"
    if not p.is_file():
        return ""
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("apikey:"):
            return s.split(":", 1)[1].strip()
    return ""


def bazarr_ping(base: str, key: str) -> None:
    url = base.rstrip("/") + "/api/system/ping"
    req = urllib.request.Request(url, headers={"X-API-KEY": key})
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status != 200:
            raise urllib.error.HTTPError(url, resp.status, "ping", hdrs=resp.headers, fp=None)


def _external_path(subtitles: object) -> str | None:
    if not isinstance(subtitles, list):
        return None
    for s in subtitles:
        if not isinstance(s, dict):
            continue
        p = (s.get("path") or "").strip()
        if p and p.lower() != "none":
            return p
    return None


def _pick_lang(missing: object, fallback: str) -> str:
    if isinstance(missing, list) and missing:
        first = missing[0]
        if isinstance(first, dict):
            c = first.get("code2") or first.get("language")
            if isinstance(c, str) and c.strip():
                return c.strip()
        if isinstance(first, str) and first.strip():
            return first.strip()
    return fallback


def scan_movies(base: str, key: str, cap: int) -> tuple[str | None, str | None]:
    start = 0
    page = 100
    scanned = 0
    total = None
    while scanned < cap:
        chunk = min(page, cap - scanned)
        data = _get(base, f"/api/movies?start={start}&length={chunk}", key)
        if not isinstance(data, dict):
            break
        total = int(data.get("total") or 0)
        rows = data.get("data") or []
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            p = _external_path(row.get("subtitles"))
            if p:
                return p, row.get("title") or ""
        scanned += len(rows)
        start += len(rows)
        if start >= total:
            break
    return None, None


def scan_episodes(base: str, key: str, max_series: int) -> tuple[str | None, str | None]:
    ser = _get(base, f"/api/series?start=0&length={max_series}", key)
    if not isinstance(ser, dict):
        return None, None
    for show in ser.get("data") or []:
        if not isinstance(show, dict):
            continue
        sid = show.get("sonarrSeriesId")
        title = show.get("title") or ""
        if sid is None:
            continue
        eps = _get(base, f"/api/episodes?seriesid%5B%5D={int(sid)}", key)
        rows = eps.get("data") if isinstance(eps, dict) else eps
        if not isinstance(rows, list):
            continue
        for ep in rows:
            if not isinstance(ep, dict):
                continue
            p = _external_path(ep.get("subtitles"))
            if p:
                return p, f"{title} / {ep.get('title') or ep.get('episode')}"
    return None, None


def scan_target_movie(base: str, key: str, radarr_id: int) -> tuple[str | None, str | None]:
    data = _get(base, f"/api/movies?radarrid%5B%5D={radarr_id}&start=0&length=10", key)
    if not isinstance(data, dict):
        return None, None
    rows = data.get("data") or []
    label = f"radarrId={radarr_id}"
    for row in rows:
        if not isinstance(row, dict):
            continue
        if int(row.get("radarrId") or -1) != radarr_id:
            continue
        label = row.get("title") or label
        p = _external_path(row.get("subtitles"))
        if p:
            return p, label
    if rows:
        return None, label
    return None, f"{label} (not in Bazarr yet)"


def scan_target_episode(base: str, key: str, episode_id: int) -> tuple[str | None, str | None]:
    eps = _get(base, f"/api/episodes?episodeid%5B%5D={episode_id}", key)
    rows = eps.get("data") if isinstance(eps, dict) else eps
    if not isinstance(rows, list) or not rows:
        return None, f"sonarrEpisodeId={episode_id}"
    ep = rows[0]
    if not isinstance(ep, dict):
        return None, f"sonarrEpisodeId={episode_id}"
    p = _external_path(ep.get("subtitles"))
    label = f"S{ep.get('season')}E{ep.get('episode')} {ep.get('title') or ''}".strip()
    return p, label or f"sonarrEpisodeId={episode_id}"


def trigger_targets(
    base: str,
    key: str,
    *,
    radarr_movie_id: int | None,
    sonarr_episode_id: int | None,
    fallback_lang: str = "en",
) -> None:
    if radarr_movie_id is not None:
        _patch(
            base,
            f"/api/movies?{urllib.parse.urlencode({'radarrid': radarr_movie_id, 'action': 'sync'})}",
            key,
        )
        _patch(
            base,
            f"/api/movies?{urllib.parse.urlencode({'radarrid': radarr_movie_id, 'action': 'search-missing'})}",
            key,
        )
    if sonarr_episode_id is not None:
        eps = _get(
            base,
            f"/api/episodes?{urllib.parse.urlencode([('episodeid[]', sonarr_episode_id)])}",
            key,
        )
        rows = eps.get("data") if isinstance(eps, dict) else eps
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise RuntimeError(f"Bazarr has no episode row for sonarrEpisodeId={sonarr_episode_id}")
        ep = rows[0]
        sid = ep.get("sonarrSeriesId")
        if sid is None:
            raise RuntimeError(f"Bazarr episode {sonarr_episode_id} missing sonarrSeriesId")
        lang = _pick_lang(ep.get("missing_subtitles"), fallback_lang)
        q = urllib.parse.urlencode(
            {
                "seriesid": int(sid),
                "episodeid": sonarr_episode_id,
                "language": lang,
                "hi": "false",
                "forced": "false",
            }
        )
        _patch(base, f"/api/episodes/subtitles?{q}", key)


def poll_until_subtitle_paths(
    base: str,
    key: str,
    *,
    radarr_movie_id: int | None,
    sonarr_episode_id: int | None,
    poll_sec: int,
    interval: int,
    scan_movies_max: int = 800,
    scan_series_max: int = 25,
) -> tuple[bool, str]:
    """Return (ok, detail). For two targets, both must show an external path."""
    deadline = time.monotonic() + max(1, poll_sec)
    broad = radarr_movie_id is None and sonarr_episode_id is None

    while time.monotonic() < deadline:
        try:
            if broad:
                mp, lab = scan_movies(base, key, scan_movies_max)
                if not mp:
                    mp, lab = scan_episodes(base, key, scan_series_max)
                if mp:
                    return True, (lab or mp)
            else:
                mp = ep = None
                if radarr_movie_id is not None:
                    mp, _ = scan_target_movie(base, key, radarr_movie_id)
                if sonarr_episode_id is not None:
                    ep, _ = scan_target_episode(base, key, sonarr_episode_id)
                need_m = radarr_movie_id is not None
                need_e = sonarr_episode_id is not None
                if ((not need_m) or (mp is not None)) and ((not need_e) or (ep is not None)):
                    parts: list[str] = []
                    if mp:
                        parts.append(f"movie={mp}")
                    if ep:
                        parts.append(f"episode={ep}")
                    return True, "; ".join(parts)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            return False, f"Bazarr API error while polling: {e}"
        time.sleep(max(1, interval))
    return False, "timeout waiting for non-empty subtitle path in Bazarr API"


def _target_radarr_movie_id() -> int | None:
    raw = (os.environ.get("BAZARR_E2E_TARGET_RADARR_MOVIE_ID") or "").strip()
    if not raw:
        return None
    return int(raw)


def _target_sonarr_episode_id() -> int | None:
    raw = (os.environ.get("BAZARR_E2E_TARGET_SONARR_EPISODE_ID") or "").strip()
    if not raw:
        return None
    return int(raw)


def trigger_target_downloads_from_env() -> int:
    base = os.environ.get("BAZARR_PUBLIC", "").rstrip("/")
    key = os.environ.get("BAZARR_API_KEY", "")
    if not base or not key:
        print("BAZARR_PUBLIC and BAZARR_API_KEY required", file=sys.stderr)
        return 1
    try:
        rid = _target_radarr_movie_id()
        eid = _target_sonarr_episode_id()
    except ValueError as e:
        print(f"Invalid target id env: {e}", file=sys.stderr)
        return 1
    if rid is None and eid is None:
        print(
            "No targets: set BAZARR_E2E_TARGET_RADARR_MOVIE_ID and/or BAZARR_E2E_TARGET_SONARR_EPISODE_ID",
            file=sys.stderr,
        )
        return 1
    fallback_lang = (os.environ.get("BAZARR_E2E_TARGET_LANGUAGE") or "en").strip() or "en"
    try:
        trigger_targets(base, key, radarr_movie_id=rid, sonarr_episode_id=eid, fallback_lang=fallback_lang)
        if rid is not None:
            print(f"triggered movie radarrId={rid} (sync + search-missing)")
        if eid is not None:
            print(f"triggered episode sonarrEpisodeId={eid}")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"trigger HTTP/API error: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def probe_from_env() -> int:
    base = os.environ.get("BAZARR_PUBLIC", "").rstrip("/")
    key = os.environ.get("BAZARR_API_KEY", "")
    if not base or not key:
        print("BAZARR_PUBLIC and BAZARR_API_KEY required", file=sys.stderr)
        return 1
    try:
        cap = int(os.environ.get("BAZARR_E2E_SCAN_MOVIES_MAX", "800"))
    except ValueError:
        cap = 800
    try:
        max_series = int(os.environ.get("BAZARR_E2E_SCAN_SERIES_MAX", "25"))
    except ValueError:
        max_series = 25
    try:
        rid = _target_radarr_movie_id()
        eid = _target_sonarr_episode_id()
    except ValueError as e:
        print(f"Invalid target id env: {e}", file=sys.stderr)
        return 1

    try:
        path: str | None = None
        label: str | None = None
        if rid is not None:
            path, label = scan_target_movie(base, key, rid)
        if not path and eid is not None:
            path, label = scan_target_episode(base, key, eid)
        if not path and rid is None and eid is None:
            path, label = scan_movies(base, key, cap)
            if not path:
                path, label = scan_episodes(base, key, max_series)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"HTTP/API error: {e}", file=sys.stderr)
        return 1
    if path:
        print(f"OK\t{path}\t{label or ''}")
        return 0
    return 3


def cli_main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--trigger-target-downloads":
        return trigger_target_downloads_from_env()
    return probe_from_env()
