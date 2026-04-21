#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from arr_retry.client import ArrClient, read_api_key_from_config_xml
from arr_retry.qbittorrent import QBittorrentClient


def _load_dotenv() -> None:
    candidates = (Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env")
    for path in candidates:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            os.environ.setdefault(key, value)


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


def _extract_download_id(queue_item: dict[str, Any]) -> str:
    for key in ("downloadId", "trackedDownloadId", "downloadClientId"):
        value = queue_item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


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
        self.health_log_file = Path(
            os.environ.get("ARR_HEALTH_LOG_FILE", "/workspace/data/arr-retry/health-last-run.log")
        )

    def qb_torrents_by_hash(self) -> dict[str, Any]:
        if self.qb is None:
            return {}
        self.qb.login()
        return {t.torrent_hash: t for t in self.qb.torrents_info()}

    def search_queue(self, name_substring: str) -> list[QueueMatch]:
        needle = name_substring.lower().strip()
        if not needle:
            return []

        qb_by_hash = self.qb_torrents_by_hash()
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
                item_id = item.get(item_label)
                queue_id = item.get("id")
                if not isinstance(item_id, int) or not isinstance(queue_id, int):
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
        return [_extract_release_fields(r) for r in releases if isinstance(r, dict)]

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
            try:
                matches = [asdict(m) for m in self.ctx.search_queue(name)]
                for match in matches:
                    match["releases"] = self.ctx.releases_for_item(match["app"], int(match["item_id"]))
                return self._write_json({"matches": matches})
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
  <p>Search a stuck queue item, inspect candidates, and manually grab a specific release via Arr API.</p>
  <div>
    <input id="name" size="60" placeholder="e.g. Crazy.Ex-Girlfriend.S04E02.720p.WEB.h264-TBS" />
    <button onclick="search()">Search</button>
  </div>
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
    function fmtBytes(n) {
      const v = Number(n || 0);
      if (v < 1024) return v + ' B';
      if (v < 1024*1024) return (v/1024).toFixed(1) + ' KiB';
      if (v < 1024*1024*1024) return (v/1024/1024).toFixed(1) + ' MiB';
      return (v/1024/1024/1024).toFixed(2) + ' GiB';
    }
    async function search() {
      const name = document.getElementById('name').value.trim();
      if (!name) return;
      statusEl.textContent = 'Searching...';
      resultsEl.innerHTML = '';
      const res = await fetch('/api/search?name=' + encodeURIComponent(name));
      const data = await res.json();
      if (!res.ok || data.error) {
        statusEl.innerHTML = '<span class=\"error\">' + (data.error || 'Search failed') + '</span>';
        return;
      }
      const matches = data.matches || [];
      statusEl.innerHTML = '<span class=\"ok\">Found ' + matches.length + ' queue match(es)</span>';
      for (const m of matches) {
        const div = document.createElement('div');
        div.className = 'item';
        div.innerHTML = `
          <div><strong>${m.app.toUpperCase()}</strong> - ${m.title}</div>
          <div>QueueID=${m.queue_id} ItemID=${m.item_id} DownloadHash=${m.download_id || '-'}</div>
          <div>Status=${m.status} / ${m.tracked_state} / ${m.tracked_status}</div>
          <div>qBittorrent: state=${m.qb_state || '-'} seeds=${m.qb_seeders} dlspeed=${fmtBytes(m.qb_dlspeed_bps)}/s avg=${fmtBytes(m.qb_avg_bps)}/s progress=${(Number(m.qb_progress||0)*100).toFixed(1)}%</div>
          <table><thead><tr><th>Title</th><th>Seeders</th><th>Leechers</th><th>Size</th><th>Approved</th><th>Allowed</th><th>Rejections</th><th>Action</th></tr></thead><tbody id=\"rels_${m.app}_${m.item_id}\"></tbody></table>
        `;
        resultsEl.appendChild(div);
        const tbody = div.querySelector('#rels_' + m.app + '_' + m.item_id);
        for (const r of (m.releases || [])) {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td>${r.title || ''}</td>
            <td>${r.seeders}</td>
            <td>${r.leechers}</td>
            <td>${fmtBytes(r.size)}</td>
            <td>${r.approved}</td>
            <td>${r.downloadAllowed}</td>
            <td>${(r.rejections || []).join(' | ')}</td>
            <td><button>Grab</button></td>
          `;
          tr.querySelector('button').onclick = () => grab(m.app, m.item_id, r.guid);
          tbody.appendChild(tr);
        }
      }
    }
    async function grab(app, itemId, guid) {
      statusEl.textContent = 'Sending grab...';
      const res = await fetch('/api/grab', {
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
      const res = await fetch('/api/health-log?lines=300');
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
    host = os.environ.get("ARR_HEALTH_UI_HOST", "127.0.0.1")
    port = int(os.environ.get("ARR_HEALTH_UI_PORT", "8091"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[torrent-health-ui] listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
