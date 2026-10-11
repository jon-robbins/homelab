from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from homelab_workers.shared import qbt_exclusive as ex
from homelab_workers.shared.qbt_events_handlers import dispatch_torrent_finished
from homelab_workers.shared.qbt_events_state import read_session
from homelab_workers.shared.qbittorrent import QBittorrentClient
from homelab_workers.shared.secrets import bootstrap_container_secrets

_EVENTS_LOCK = threading.RLock()
_SERVICE_NAME = "homelab-qbt-events"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or not raw.strip() else int(raw)


def _session_path() -> str:
    return os.environ.get("QBT_EVENTS_SESSION_PATH", "/state/session.json")


def _status_path() -> str:
    return os.environ.get("QBT_EVENTS_STATUS_PATH", "/state/status.json")


def _qbt_client() -> QBittorrentClient:
    return QBittorrentClient(
        os.environ.get("QBITTORRENT_INTERNAL_URL", "http://qbittorrent:8080"),
        os.environ.get("QBITTORRENT_USERNAME", ""),
        os.environ.get("QBITTORRENT_PASSWORD", ""),
    )


def _write_status_heartbeat(*, last_reenforce_at: int) -> None:
    target = Path(_status_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": int(time.time()),
        "last_reenforce_at": last_reenforce_at,
    }
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, target)


def _is_upstream_error(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.HTTPError, RuntimeError, OSError))


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length).decode("utf-8")
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    return body


def _require_confirm(body: dict[str, Any]) -> None:
    if body.get("confirm") is not True:
        raise ValueError("confirm_required")


def health_payload() -> tuple[int, dict[str, Any]]:
    return 200, {"ok": True, "service": _SERVICE_NAME}


def healthcheck() -> int:
    status, health = health_payload()
    print(f"{_SERVICE_NAME}: {health['service']}", file=sys.stdout if status == 200 else sys.stderr)
    return 0 if status == 200 else 1


def _reenforce_loop() -> None:
    interval = _env_int("QBT_EVENTS_REENFORCE_SECONDS", 45)
    while True:
        session_path = _session_path()
        if read_session(session_path) is not None:
            try:
                with _EVENTS_LOCK:
                    with _qbt_client() as qbt:
                        ex.reenforce_exclusive(qbt, session_path=session_path)
                _write_status_heartbeat(last_reenforce_at=int(time.time()))
            except Exception as exc:
                print(f"{_SERVICE_NAME}: reenforce ERROR: {exc}", file=sys.stderr)
        time.sleep(interval)


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                status, body = health_payload()
                self._send_json(status, body)
                return
            if self.path == "/v1/exclusive/status":
                with _EVENTS_LOCK:
                    self._send_json(200, ex.status_payload(_session_path()))
                return
            self._send_json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            if self.path == "/v1/torrent-finished":
                self._handle_torrent_finished()
                return
            if self.path == "/v1/exclusive/start":
                self._handle_exclusive_start()
                return
            if self.path == "/v1/exclusive/cancel":
                self._handle_exclusive_cancel()
                return
            self._send_json(404, {"ok": False, "error": "not_found"})

        def _handle_torrent_finished(self) -> None:
            try:
                body = _read_json_body(self)
                hash_value = body.get("hash")
                if not isinstance(hash_value, str) or not hash_value.strip():
                    raise ValueError("hash is required")
            except (ValueError, json.JSONDecodeError) as exc:
                if str(exc) == "confirm_required":
                    self._send_json(400, {"ok": False, "error": "confirm_required"})
                    return
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            try:
                with _EVENTS_LOCK:
                    with _qbt_client() as qbt:
                        result = dispatch_torrent_finished(
                            qbt,
                            hash_value=hash_value.strip(),
                            session_path=_session_path(),
                        )
            except Exception as exc:
                if _is_upstream_error(exc):
                    self._send_json(502, {"ok": False, "error": str(exc)})
                    return
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)

        def _handle_exclusive_start(self) -> None:
            try:
                body = _read_json_body(self)
                _require_confirm(body)
                name_contains = body.get("name_contains")
                if not isinstance(name_contains, str):
                    raise ValueError("name_contains is required")
            except ValueError as exc:
                if str(exc) == "confirm_required":
                    self._send_json(400, {"ok": False, "error": "confirm_required"})
                    return
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            except json.JSONDecodeError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            try:
                with _EVENTS_LOCK:
                    with _qbt_client() as qbt:
                        result = ex.start_exclusive(
                            qbt,
                            name_contains=name_contains,
                            session_path=_session_path(),
                        )
            except ex.SessionActiveError:
                self._send_json(409, {"ok": False, "error": "SESSION_ACTIVE"})
                return
            except ex.NoMatchesError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                if _is_upstream_error(exc):
                    self._send_json(502, {"ok": False, "error": str(exc)})
                    return
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)

        def _handle_exclusive_cancel(self) -> None:
            try:
                body = _read_json_body(self)
                _require_confirm(body)
            except ValueError as exc:
                if str(exc) == "confirm_required":
                    self._send_json(400, {"ok": False, "error": "confirm_required"})
                    return
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            except json.JSONDecodeError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            try:
                with _EVENTS_LOCK:
                    with _qbt_client() as qbt:
                        result = ex.cancel_exclusive(qbt, session_path=_session_path())
            except ex.NoSessionError:
                self._send_json(404, {"ok": False, "error": "NO_SESSION"})
                return
            except Exception as exc:
                if _is_upstream_error(exc):
                    self._send_json(502, {"ok": False, "error": str(exc)})
                    return
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send_json(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return ThreadingHTTPServer((host, port), Handler)


def serve(*, host: str, port: int) -> None:
    threading.Thread(target=_reenforce_loop, daemon=True).start()
    make_server(host, port).serve_forever()


def main(argv: list[str] | None = None) -> int:
    bootstrap_container_secrets()
    parser = argparse.ArgumentParser(description="qBittorrent completion events and exclusive priority API")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--serve", action="store_true", help="Serve the events HTTP API")
    actions.add_argument("--healthcheck", action="store_true", help="Check service health")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8101)
    args = parser.parse_args(argv)

    if args.healthcheck:
        return healthcheck()
    serve(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
