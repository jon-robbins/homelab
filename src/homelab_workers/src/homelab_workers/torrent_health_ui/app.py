#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from homelab_workers.shared.arr import ArrClient, read_api_key_from_config_xml
from homelab_workers.shared.qbittorrent import QBittorrentClient
from homelab_workers.shared.dotenv import load_dotenv_into_environ
from homelab_workers.shared.logging import setup_logging


def _load_dotenv() -> None:
    candidates = (Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env")
    for path in candidates:
        load_dotenv_into_environ(path)


@dataclass(frozen=True)
class QueueMatch:
    app: str
    title: str
    queue_id: int
    item_id: int
    item_label: str
    download_id: str
    status: str
    tracked_state: str
    tracked_status: str
    qb_state: str
    qb_seeders: int
    qb_leechers: int
    qb_dlspeed_bps: float
    qb_avg_bps: float
    qb_progress: float


def _int_field(value: Any) -> int | None:
    """Sonarr/Radarr JSON sometimes uses int-like strings; normalize for queue matching."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 10)
        except ValueError:
            return None
    return None


def _extract_download_id(queue_item: dict[str, Any]) -> str:
    for key in ("downloadId", "trackedDownloadId", "downloadClientId"):
        value = queue_item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


_HINT_SXXEYY = re.compile(r"s\d{1,2}e\d{1,3}\b", re.IGNORECASE)


def _extract_release_fields(release: dict[str, Any]) -> dict[str, Any]:
    return {
        "guid": release.get("guid"),
        "title": release.get("title"),
        "indexer": release.get("indexer"),
        "seeders": int(release.get("seeders") or 0),
        "leechers": int(release.get("leechers") or 0),
        "size": int(release.get("size") or 0),
        "approved": bool(release.get("approved")),
        "downloadAllowed": bool(release.get("downloadAllowed", True)),
        "rejections": release.get("rejections") or [],
    }


class AppContext:
    def __init__(self) -> None:
        _load_dotenv()
        timeout = int(os.environ.get("ARR_UI_HTTP_TIMEOUT_SECONDS", "25"))

        sonarr_key = os.environ.get("SONARR_API_KEY") or read_api_key_from_config_xml(
            os.environ.get("SONARR_CONFIG_PATH", "data/sonarr/config.xml")
        )
        radarr_key = os.environ.get("RADARR_API_KEY") or read_api_key_from_config_xml(
            os.environ.get("RADARR_CONFIG_PATH", "data/radarr/config.xml")
        )

        if not sonarr_key:
            raise RuntimeError("SONARR_API_KEY missing")
        if not radarr_key:
            raise RuntimeError("RADARR_API_KEY missing")

        self.sonarr = ArrClient(
            os.environ.get("SONARR_URL", "http://127.0.0.1:8989/sonarr"), sonarr_key, timeout
        )
        self.radarr = ArrClient(os.environ.get("RADARR_URL", "http://127.0.0.1:7878"), radarr_key, timeout)

        self.qb: QBittorrentClient | None = None
        qb_user = os.environ.get("QBITTORRENT_USERNAME", "")
        qb_pass = os.environ.get("QBITTORRENT_PASSWORD", "")
        if qb_user and qb_pass:
            self.qb = QBittorrentClient(
                os.environ.get("QBITTORRENT_URL", "http://127.0.0.1:8080"),
                qb_user,
                qb_pass,
                timeout,
            )
        self.health_log_file = Path(os.environ.get("TORRENT_HEALTH_LOG_FILE", "/workspace/data/torrent-health/health.log"))

    def qb_torrents_by_hash(self) -> dict[str, Any]:
        if self.qb is None:
            return {}
        self.qb.login()
        return {t.torrent_hash: t for t in self.qb.torrents_info()}

    def qb_torrents_matching_name(self, needle: str, qb_by_hash: dict[str, Any]) -> list[dict[str, Any]]:
        n = needle.lower().strip()
        if not n:
            return []
        rows: list[dict[str, Any]] = []
        for t in qb_by_hash.values():
            if n not in str(t.name or "").lower():
                continue
            rows.append(
                {
                    "hash": t.torrent_hash,
                    "name": t.name,
                    "progress": t.progress,
                    "state": t.state,
                    "dlspeed_bps": t.dlspeed_bps,
                    "num_seeds": t.num_seeds,
                    "amount_left": t.amount_left,
                }
            )
        rows.sort(key=lambda r: (-float(r["progress"]), str(r["name"])))
        return rows

    def search_queue(self, name_substring: str, qb_by_hash: dict[str, Any] | None = None) -> list[QueueMatch]:
        needle = name_substring.lower().strip()
        if not needle:
            return []

        qb_by_hash = qb_by_hash if qb_by_hash is not None else self.qb_torrents_by_hash()
        results: list[QueueMatch] = []

        for app_name, client, item_label in (
            ("sonarr", self.sonarr, "episodeId"),
            ("radarr", self.radarr, "movieId"),
        ):
            queue = client.get("/queue/details", {"all": "true"})
            if not isinstance(queue, list):
                continue
            for item in queue:
                title = str(item.get("title") or "")
                dl_title = str(item.get("downloadClientTitle") or "")
                blob = f"{title} {dl_title}".lower()
                if needle not in blob:
                    continue
                item_id = _int_field(item.get(item_label))
                queue_id = _int_field(item.get("id"))
                if item_id is None or queue_id is None:
                    continue
                dl_id = _extract_download_id(item)
                qb_state = ""
                qb_seeders = 0
                qb_leechers = 0
                qb_dlspeed = 0.0
                qb_avg = 0.0
                qb_progress = 0.0
                qb_torrent = qb_by_hash.get(dl_id)
                if qb_torrent is not None:
                    qb_state = qb_torrent.state
                    qb_seeders = qb_torrent.num_seeds
                    qb_leechers = 0
                    qb_dlspeed = qb_torrent.dlspeed_bps
                    qb_avg = qb_torrent.average_download_bps
                    qb_progress = qb_torrent.progress
                results.append(
                    QueueMatch(
                        app=app_name,
                        title=title,
                        queue_id=queue_id,
                        item_id=item_id,
                        item_label=item_label,
                        download_id=dl_id,
                        status=str(item.get("status") or ""),
                        tracked_state=str(item.get("trackedDownloadState") or ""),
                        tracked_status=str(item.get("trackedDownloadStatus") or ""),
                        qb_state=qb_state,
                        qb_seeders=qb_seeders,
                        qb_leechers=qb_leechers,
                        qb_dlspeed_bps=qb_dlspeed,
                        qb_avg_bps=qb_avg,
                        qb_progress=qb_progress,
                    )
                )

        return results

    def releases_for_item(self, app: str, item_id: int) -> list[dict[str, Any]]:
        client = self.sonarr if app == "sonarr" else self.radarr
        query_key = "episodeId" if app == "sonarr" else "movieId"
        releases = client.get("/release", {query_key: item_id})
        if not isinstance(releases, list):
            return []
        out = [_extract_release_fields(r) for r in releases if isinstance(r, dict)]
        out.sort(key=lambda r: (-int(r["seeders"]), -int(r["size"])))
        return out

    def parse_library_targets(self, title: str) -> list[dict[str, Any]]:
        """Map a release-style title to Sonarr episodeIds / Radarr movieIds via /parse."""
        t = title.strip()
        if not t:
            return []
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()

        try:
            sd = self.sonarr.get("/parse", {"title": t})
        except Exception:
            sd = None
        if isinstance(sd, dict):
            series = sd.get("series")
            s_title = ""
            if isinstance(series, dict):
                s_title = str(series.get("title") or series.get("sortTitle") or "")
            for ep in sd.get("episodes") or []:
                if not isinstance(ep, dict):
                    continue
                eid = _int_field(ep.get("id"))
                if eid is None:
                    continue
                key = ("sonarr", eid)
                if key in seen:
                    continue
                seen.add(key)
                sn, en = ep.get("seasonNumber"), ep.get("episodeNumber")
                lbl = s_title
                if isinstance(sn, int) and isinstance(en, int):
                    lbl = f"{s_title} S{sn:02d}E{en:02d}".strip()
                out.append(
                    {
                        "app": "sonarr",
                        "item_id": eid,
                        "item_label": "episodeId",
                        "label": (lbl or f"episode {eid}").strip(),
                    }
                )

        try:
            rd = self.radarr.get("/parse", {"title": t})
        except Exception:
            rd = None
        if isinstance(rd, dict):
            movie = rd.get("movie")
            if isinstance(movie, dict):
                mid = _int_field(movie.get("id"))
                if mid is not None:
                    key = ("radarr", mid)
                    if key not in seen:
                        seen.add(key)
                        mt = str(movie.get("title") or movie.get("originalTitle") or "")
                        yr = movie.get("year")
                        lbl = f"{mt} ({yr})" if yr else mt
                        out.append(
                            {
                                "app": "radarr",
                                "item_id": mid,
                                "item_label": "movieId",
                                "label": (lbl.strip() or f"movie {mid}"),
                            }
                        )

        return out

    def releases_with_optional_search(
        self, app: str, item_id: int, trigger_search: bool
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch releases; optionally run indexer search first and poll until results or timeout."""
        notes: list[str] = []
        client = self.sonarr if app == "sonarr" else self.radarr
        if trigger_search:
            try:
                if app == "sonarr":
                    client.post("/command", {"name": "EpisodeSearch", "episodeIds": [item_id]})
                    notes.append("Sonarr EpisodeSearch sent")
                else:
                    client.post("/command", {"name": "MoviesSearch", "movieIds": [item_id]})
                    notes.append("Radarr MoviesSearch sent")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"Indexer search command failed: {exc}")

        if not trigger_search:
            rel = self.releases_for_item(app, item_id)
            if not rel:
                notes.append("No cached releases; enable indexer search or use Interactive Search in Sonarr/Radarr.")
            return rel, (" ".join(notes) if notes else None)

        wait_sec = int(os.environ.get("ARR_UI_RELEASE_WAIT_SECONDS", "60"))
        poll_interval = float(os.environ.get("ARR_UI_RELEASE_POLL_SECONDS", "2.5"))
        deadline = time.monotonic() + wait_sec
        final: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            final = self.releases_for_item(app, item_id)
            if final:
                break
            time.sleep(poll_interval)
        if not final:
            notes.append(f"No releases after {wait_sec}s (indexers, filters, or slow Prowlarr).")
        return final, (" ".join(notes) if notes else None)

    def resolve_tv_episode_from_parts(self, series: str, season: int, episode: int) -> tuple[int | None, str]:
        """Use Sonarr /parse with 'Show Name SxxEyy' when the main search box cannot infer an episode."""
        slug = f"{series.strip()} S{season:02d}E{episode:02d}"
        data = self.sonarr.get("/parse", {"title": slug})
        if not isinstance(data, dict):
            return None, slug
        eps = data.get("episodes") or []
        if not isinstance(eps, list) or not eps or not isinstance(eps[0], dict):
            return None, slug
        eid = _int_field(eps[0].get("id"))
        sdata = data.get("series")
        st = str(sdata.get("title") or "") if isinstance(sdata, dict) else ""
        if eid is None:
            return None, slug
        label = f"{st} S{season:02d}E{episode:02d}".strip() or slug
        return eid, label

    def grab_release(self, app: str, item_id: int, guid: str) -> dict[str, Any]:
        client = self.sonarr if app == "sonarr" else self.radarr
        query_key = "episodeId" if app == "sonarr" else "movieId"
        releases = client.get("/release", {query_key: item_id})
        if not isinstance(releases, list):
            raise RuntimeError("release lookup failed")
        selected = None
        for rel in releases:
            if not isinstance(rel, dict):
                continue
            if str(rel.get("guid") or "") == guid:
                selected = rel
                break
        if selected is None:
            raise RuntimeError("selected release not found")
        response = client.post("/release", selected)
        return response if isinstance(response, dict) else {}

    def read_health_log(self, tail_lines: int = 250) -> dict[str, Any]:
        if tail_lines < 1:
            tail_lines = 1
        if tail_lines > 2000:
            tail_lines = 2000
        if not self.health_log_file.exists():
            return {
                "exists": False,
                "path": str(self.health_log_file),
                "updated_at": None,
                "content": "",
            }
        try:
            text = self.health_log_file.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"failed reading health log: {exc}") from exc
        lines = text.splitlines()
        tailed = "\n".join(lines[-tail_lines:])
        return {
            "exists": True,
            "path": str(self.health_log_file),
            "updated_at": int(self.health_log_file.stat().st_mtime),
            "content": tailed,
        }


class Handler(BaseHTTPRequestHandler):
    ctx = AppContext()

    def _write_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            return self._write_html(INDEX_HTML)
        if parsed.path == "/api/search":
            params = urllib.parse.parse_qs(parsed.query)
            name = (params.get("name") or [""])[0]
            if not name.strip():
                return self._write_json({"error": "name is required"}, HTTPStatus.BAD_REQUEST)
            raw_trig = (params.get("triggerSearch") or ["1"])[0]
            trigger_search = str(raw_trig).strip().lower() not in ("0", "false", "no", "off")
            try:
                qb_by_hash = self.ctx.qb_torrents_by_hash()
                matches = [asdict(m) for m in self.ctx.search_queue(name.strip(), qb_by_hash)]
                for match in matches:
                    rels, note = self.ctx.releases_with_optional_search(
                        match["app"], int(match["item_id"]), trigger_search
                    )
                    match["releases"] = rels
                    if note:
                        match["searchNote"] = note
                qb_torrents = self.ctx.qb_torrents_matching_name(name.strip(), qb_by_hash)
                queue_keys = {(m["app"], int(m["item_id"])) for m in matches}
                parsed_out: list[dict[str, Any]] = []
                for target in self.ctx.parse_library_targets(name.strip()):
                    if (target["app"], int(target["item_id"])) in queue_keys:
                        continue
                    rels, note = self.ctx.releases_with_optional_search(
                        target["app"], int(target["item_id"]), trigger_search
                    )
                    row = {**target, "releases": rels}
                    if note:
                        row["searchNote"] = note
                    parsed_out.append(row)
                hints: list[str] = []
                if not parsed_out and not matches and _HINT_SXXEYY.search(name.strip()) is None:
                    hints.append(
                        "Add season/episode (e.g. S04E06) or a full release name so Sonarr/Radarr "
                        "parse can map to a library TV episode or movie."
                    )
                return self._write_json(
                    {
                        "matches": matches,
                        "parsed": parsed_out,
                        "qbTorrents": qb_torrents,
                        "hints": hints,
                        "triggerSearch": trigger_search,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                return self._write_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
        if parsed.path == "/api/resolve-tv":
            params = urllib.parse.parse_qs(parsed.query)
            series = (params.get("series") or [""])[0].strip()
            try:
                season = int((params.get("season") or ["-1"])[0])
                epn = int((params.get("episode") or ["-1"])[0])
            except ValueError:
                return self._write_json({"error": "season and episode must be integers"}, HTTPStatus.BAD_REQUEST)
            if not series or season < 0 or epn < 0:
                return self._write_json(
                    {"error": "Query params series, season, episode are required (non-negative)."},
                    HTTPStatus.BAD_REQUEST,
                )
            raw_trig = (params.get("triggerSearch") or ["1"])[0]
            trigger_search = str(raw_trig).strip().lower() not in ("0", "false", "no", "off")
            try:
                eid, label = self.ctx.resolve_tv_episode_from_parts(series, season, epn)
                if eid is None:
                    return self._write_json(
                        {
                            "error": (
                                f"Sonarr could not map {series!r} S{season:02d}E{epn:02d} to a library episode "
                                "(check spelling vs library title)."
                            )
                        },
                        HTTPStatus.NOT_FOUND,
                    )
                rels, note = self.ctx.releases_with_optional_search("sonarr", eid, trigger_search)
                return self._write_json(
                    {
                        "app": "sonarr",
                        "episodeId": eid,
                        "label": label,
                        "releases": rels,
                        "searchNote": note,
                        "triggerSearch": trigger_search,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                return self._write_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
        if parsed.path == "/api/health-log":
            params = urllib.parse.parse_qs(parsed.query)
            raw_lines = (params.get("lines") or ["250"])[0]
            try:
                lines = int(raw_lines)
            except ValueError:
                lines = 250
            try:
                return self._write_json(self.ctx.read_health_log(lines))
            except Exception as exc:  # noqa: BLE001
                return self._write_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
        self.send_error(HTTPStatus.NOT_FOUND.value)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/grab":
            self.send_error(HTTPStatus.NOT_FOUND.value)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        try:
            payload = json.loads(body)
        except Exception:  # noqa: BLE001
            return self._write_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
        app = str(payload.get("app") or "").strip().lower()
        guid = str(payload.get("guid") or "").strip()
        item_id = payload.get("itemId")
        if app not in {"sonarr", "radarr"}:
            return self._write_json({"error": "app must be sonarr or radarr"}, HTTPStatus.BAD_REQUEST)
        if isinstance(item_id, str) and item_id.strip().isdigit():
            item_id = int(item_id.strip(), 10)
        if isinstance(item_id, float) and item_id.is_integer():
            item_id = int(item_id)
        if not isinstance(item_id, int):
            return self._write_json({"error": "itemId must be integer"}, HTTPStatus.BAD_REQUEST)
        if not guid:
            return self._write_json({"error": "guid is required"}, HTTPStatus.BAD_REQUEST)
        try:
            response = self.ctx.grab_release(app, item_id, guid)
            return self._write_json({"ok": True, "response": response})
        except Exception as exc:  # noqa: BLE001
            return self._write_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Torrent Health Manual Grab</title>
  <style>
    body { font-family: sans-serif; margin: 24px; background: #111; color: #eee; }
    input, button { padding: 8px; margin-right: 8px; }
    button { cursor: pointer; }
    .item { border: 1px solid #444; padding: 12px; margin: 12px 0; border-radius: 8px; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; }
    th, td { border: 1px solid #333; padding: 6px; text-align: left; font-size: 13px; }
    .ok { color: #9f9; }
    .warn { color: #ffb347; }
    .error { color: #ff7f7f; }
  </style>
</head>
<body>
  <h2>Torrent Health Manual Grab</h2>
  <p>Enter a <strong>release-style title</strong> (or text that appears in the activity queue). The tool resolves your <strong>Sonarr / Radarr library</strong> episode or movie via the <code>/parse</code> API, lists indexer <strong>release alternatives</strong> (sorted by seeders then size), and can <strong>grab</strong> one without using the Arr UI.</p>
  <p class="warn"><strong>Activity queue</strong> (<code>/queue/details</code>) matches when Sonarr/Radarr still show a download. <strong>Library parse</strong> matches when the queue is empty but the string maps to a monitored episode/movie (e.g. include <code>S04E06</code>). <strong>qBittorrent</strong> lists torrents whose name contains your search string. Use the checkbox below to run the same indexer search commands as Interactive Search (can take up to about a minute).</p>
  <div>
    <input id="name" size="60" placeholder="e.g. Crazy.Ex-Girlfriend.S04E06.720p.WEB.x264-TBS" />
    <button onclick="search()">Search</button>
    <label style="margin-left:12px;"><input type="checkbox" id="triggerSearch" checked /> Run indexer search (EpisodeSearch / MoviesSearch) and wait for releases</label>
  </div>
  <p style="margin-top:16px;">TV episode by show name + S/E (when the main search string does not resolve):</p>
  <div>
    <input id="tvSeries" placeholder="Crazy Ex-Girlfriend" size="28" />
    <label>S <input id="tvSeason" type="number" min="0" value="4" style="width:4em" /></label>
    <label>E <input id="tvEpisode" type="number" min="0" value="6" style="width:4em" /></label>
    <button type="button" onclick="resolveTv()">Load this episode</button>
  </div>
  <div id="resolveResults"></div>
  <p id="status"></p>
  <div class="item">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
      <strong>Last Health Script Run Log</strong>
      <div>
        <button onclick="refreshLog()">Refresh Log</button>
        <label><input type="checkbox" id="autoLog" checked /> Auto-refresh</label>
      </div>
    </div>
    <p id="logMeta" class="warn"></p>
    <pre id="runLog" style="max-height:260px;overflow:auto;background:#181818;border:1px solid #333;padding:8px;"></pre>
  </div>
  <div id="results"></div>
  <script>
    const statusEl = document.getElementById('status');
    const resultsEl = document.getElementById('results');
    const logEl = document.getElementById('runLog');
    const logMetaEl = document.getElementById('logMeta');
    function escHtml(s) {
      return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');
    }
    function fmtBytes(n) {
      const v = Number(n || 0);
      if (v < 1024) return v + ' B';
      if (v < 1024*1024) return (v/1024).toFixed(1) + ' KiB';
      if (v < 1024*1024*1024) return (v/1024/1024).toFixed(1) + ' MiB';
      return (v/1024/1024/1024).toFixed(2) + ' GiB';
    }
    function fillReleaseRows(tbody, app, itemId, releases) {
      for (const r of (releases || [])) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${escHtml(r.title || '')}</td>
          <td>${r.seeders}</td>
          <td>${r.leechers}</td>
          <td>${fmtBytes(r.size)}</td>
          <td>${r.approved}</td>
          <td>${r.downloadAllowed}</td>
          <td>${(r.rejections || []).join(' | ')}</td>
          <td><button>Grab</button></td>
        `;
        tr.querySelector('button').onclick = () => grab(app, itemId, r.guid);
        tbody.appendChild(tr);
      }
    }
    async function resolveTv() {
      const series = document.getElementById('tvSeries').value.trim();
      const season = document.getElementById('tvSeason').value;
      const episode = document.getElementById('tvEpisode').value;
      const rr = document.getElementById('resolveResults');
      rr.innerHTML = '';
      if (!series || season === '' || episode === '') {
        statusEl.innerHTML = '<span class="warn">Enter series name, season, and episode.</span>';
        return;
      }
      const trigger = document.getElementById('triggerSearch').checked;
      statusEl.textContent = trigger ? 'Resolving episode (indexer wait may take ~60s)...' : 'Resolving episode...';
      const res = await fetch('./api/resolve-tv?' + new URLSearchParams({series, season, episode, triggerSearch: trigger ? '1' : '0'}));
      const data = await res.json();
      if (!res.ok || data.error) {
        statusEl.innerHTML = '<span class="error">' + escHtml(data.error || 'Resolve failed') + '</span>';
        return;
      }
      statusEl.innerHTML = '<span class="ok">Resolved: ' + escHtml(data.label) + ' (episodeId=' + data.episodeId + ')</span>';
      const div = document.createElement('div');
      div.className = 'item';
      const note = data.searchNote ? '<p class="warn">' + escHtml(data.searchNote) + '</p>' : '';
      div.innerHTML = '<div><strong>SONARR</strong> — ' + escHtml(data.label) + '</div>' + note +
        '<table><thead><tr><th>Title</th><th>Seeders</th><th>Leechers</th><th>Size</th><th>Approved</th><th>Allowed</th><th>Rejections</th><th>Action</th></tr></thead><tbody id="rels_resolve_sonarr_' + data.episodeId + '"></tbody></table>';
      rr.appendChild(div);
      const tbody = div.querySelector('#rels_resolve_sonarr_' + data.episodeId);
      fillReleaseRows(tbody, 'sonarr', data.episodeId, data.releases);
    }
    async function search() {
      const name = document.getElementById('name').value.trim();
      if (!name) return;
      const trigger = document.getElementById('triggerSearch').checked;
      statusEl.textContent = trigger
        ? 'Searching (indexer search can take up to ~60s)...'
        : 'Searching...';
      resultsEl.innerHTML = '';
      const res = await fetch('./api/search?name=' + encodeURIComponent(name) + '&triggerSearch=' + (trigger ? '1' : '0'));
      const data = await res.json();
      if (!res.ok || data.error) {
        statusEl.innerHTML = '<span class=\"error\">' + (data.error || 'Search failed') + '</span>';
        return;
      }
      const matches = data.matches || [];
      const parsed = data.parsed || [];
      const qb = data.qbTorrents || [];
      const hints = data.hints || [];
      let parts = [];
      if (matches.length) parts.push(matches.length + ' activity queue');
      if (parsed.length) parts.push(parsed.length + ' library (parse)');
      if (qb.length) parts.push(qb.length + ' qBittorrent name match(es)');
      let statusHtml = '';
      if (parts.length) {
        statusHtml = '<span class="ok">Found: ' + parts.join(', ') + '</span>';
      } else {
        statusHtml = '<span class="warn">No activity queue rows, no library parse match, and no qBittorrent name matches.</span>';
      }
      if (hints.length) {
        statusHtml += ' <span class="warn">' + hints.map(escHtml).join(' ') + '</span>';
      }
      statusEl.innerHTML = statusHtml;
      for (const m of matches) {
        const div = document.createElement('div');
        div.className = 'item';
        const note = m.searchNote ? '<p class="warn">' + escHtml(m.searchNote) + '</p>' : '';
        div.innerHTML = `
          <div><strong>${m.app.toUpperCase()} — activity queue</strong> — ${escHtml(m.title)}</div>
          <div>QueueID=${m.queue_id} ItemID=${m.item_id} DownloadHash=${m.download_id || '-'}</div>
          <div>Status=${m.status} / ${m.tracked_state} / ${m.tracked_status}</div>
          <div>qBittorrent: state=${m.qb_state || '-'} seeds=${m.qb_seeders} dlspeed=${fmtBytes(m.qb_dlspeed_bps)}/s avg=${fmtBytes(m.qb_avg_bps)}/s progress=${(Number(m.qb_progress||0)*100).toFixed(1)}%</div>
          ${note}
          <table><thead><tr><th>Title</th><th>Seeders</th><th>Leechers</th><th>Size</th><th>Approved</th><th>Allowed</th><th>Rejections</th><th>Action</th></tr></thead><tbody id=\"rels_queue_${m.app}_${m.item_id}\"></tbody></table>
        `;
        resultsEl.appendChild(div);
        const tbody = div.querySelector('#rels_queue_' + m.app + '_' + m.item_id);
        fillReleaseRows(tbody, m.app, m.item_id, m.releases);
      }
      for (const p of parsed) {
        const div = document.createElement('div');
        div.className = 'item';
        const note = p.searchNote ? '<p class="warn">' + escHtml(p.searchNote) + '</p>' : '';
        div.innerHTML = `
          <div><strong>${p.app.toUpperCase()} — library parse</strong> — ${escHtml(p.label)}</div>
          <div>ItemID=${p.item_id} (${p.item_label}) — not in activity queue (or duplicate skipped)</div>
          ${note}
          <table><thead><tr><th>Title</th><th>Seeders</th><th>Leechers</th><th>Size</th><th>Approved</th><th>Allowed</th><th>Rejections</th><th>Action</th></tr></thead><tbody id=\"rels_parse_${p.app}_${p.item_id}\"></tbody></table>
        `;
        resultsEl.appendChild(div);
        const tbody = div.querySelector('#rels_parse_' + p.app + '_' + p.item_id);
        fillReleaseRows(tbody, p.app, p.item_id, p.releases);
      }
      if (qb.length) {
        const wrap = document.createElement('div');
        wrap.className = 'item';
        let rows = '';
        for (const q of qb) {
          const prog = (Number(q.progress || 0) * 100).toFixed(1);
          rows += '<tr><td>' + escHtml(q.name || '') + '</td><td><code>' + escHtml(q.hash || '') + '</code></td><td>' + prog + '%</td><td>' + escHtml(q.state || '') + '</td><td>' + fmtBytes(q.dlspeed_bps) + '/s</td><td>' + (q.num_seeds ?? '') + '</td><td>' + fmtBytes(q.amount_left) + '</td></tr>';
        }
        wrap.innerHTML = '<strong>qBittorrent torrents (name contains search)</strong><p class="warn">These are not tied to the grab buttons above unless the same episode appears in the Sonarr queue.</p><table><thead><tr><th>Name</th><th>Hash</th><th>Progress</th><th>State</th><th>DL speed</th><th>Seeds</th><th>Left</th></tr></thead><tbody>' + rows + '</tbody></table>';
        resultsEl.appendChild(wrap);
      }
    }
    async function grab(app, itemId, guid) {
      statusEl.textContent = 'Sending grab...';
      const res = await fetch('./api/grab', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({app, itemId, guid})
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        statusEl.innerHTML = '<span class=\"error\">Grab failed: ' + (data.error || 'unknown error') + '</span>';
        return;
      }
      statusEl.innerHTML = '<span class=\"ok\">Grab requested successfully.</span>';
    }
    async function refreshLog() {
      const res = await fetch('./api/health-log?lines=300');
      const data = await res.json();
      if (!res.ok || data.error) {
        logMetaEl.innerHTML = '<span class=\"error\">Failed to load log: ' + (data.error || 'unknown error') + '</span>';
        return;
      }
      if (!data.exists) {
        logMetaEl.innerHTML = '<span class=\"warn\">No run log file yet (' + data.path + '). It appears after the next worker run.</span>';
        logEl.textContent = '';
        return;
      }
      const ts = data.updated_at ? new Date(data.updated_at * 1000).toLocaleString() : 'unknown';
      logMetaEl.innerHTML = '<span class=\"ok\">Log updated: ' + ts + '</span>';
      logEl.textContent = data.content || '';
      logEl.scrollTop = logEl.scrollHeight;
    }
    refreshLog();
    setInterval(() => {
      if (document.getElementById('autoLog').checked) {
        refreshLog();
      }
    }, 5000);
  </script>
</body>
</html>
"""


def main() -> None:
    logger = setup_logging("torrent_health_ui", logging.INFO)
    host = os.environ.get("TORRENT_HEALTH_UI_HOST", "127.0.0.1")
    port = int(os.environ.get("TORRENT_HEALTH_UI_PORT", "8091"))
    server = ThreadingHTTPServer((host, port), Handler)
    logger.info("listening on http://%s:%s", host, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
