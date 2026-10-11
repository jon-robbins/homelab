from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from homelab_workers.shared.config import (
    get_config_int,
    get_config_str,
    get_config_val,
    load_worker_yaml_config,
)
from homelab_workers.shared.qbt_offload import (
    list_offload_candidates,
    plan_offload_candidate,
    run_offload_moves,
)
from homelab_workers.shared.qbt_offload_state import (
    build_qbt_offload_status,
    evaluate_qbt_offload_health,
    read_qbt_offload_status,
    write_qbt_offload_status,
)
from homelab_workers.shared.qbittorrent import QBittorrentClient
from homelab_workers.shared.secrets import bootstrap_container_secrets


_OFFLOAD_STATUS_LOCK = threading.RLock()


def _get_yaml() -> dict[str, Any]:
    return load_worker_yaml_config().get("offload", {})


def _env_int(name: str, default: int) -> int:
    yaml_key = name.replace("QBT_OFFLOAD_", "").lower()
    return get_config_int(os.environ, _get_yaml(), name, yaml_key, default)


def _get_config_list(env_key: str, yaml_key: str) -> list[str]:
    value = get_config_val(os.environ, _get_yaml(), env_key, yaml_key, [])
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [item.strip() for item in value if item.strip()]
    raise ValueError(f"{env_key} must be a comma-separated string or list of strings")


def _offload_exclusions() -> tuple[list[str], list[str]]:
    return (
        _get_config_list("QBT_OFFLOAD_EXCLUDED_TAGS", "excluded_tags"),
        _get_config_list("QBT_OFFLOAD_EXCLUDED_CATEGORIES", "excluded_categories"),
    )


def _status_path() -> str:
    return get_config_str(
        os.environ,
        _get_yaml(),
        "QBT_OFFLOAD_STATUS_PATH",
        "status_path",
        "/state/status.json",
    )


def _qbt_client() -> QBittorrentClient:
    return QBittorrentClient(
        os.environ.get("QBITTORRENT_INTERNAL_URL", "http://qbittorrent:8080"),
        os.environ.get("QBITTORRENT_USERNAME", ""),
        os.environ.get("QBITTORRENT_PASSWORD", ""),
    )


def _paths() -> tuple[str, str]:
    return (
        os.environ.get("MEDIA_NVME_PATH", "/mnt/media-nvme"),
        os.environ.get("MEDIA_HDD_PATH", "/mnt/media-hdd"),
    )


def _write_status(
    *,
    last_run: int,
    offloaded: int,
    failed: int,
    hdd_full: bool,
    pending_on_nvme: int,
) -> None:
    with _OFFLOAD_STATUS_LOCK:
        write_qbt_offload_status(
            _status_path(),
            build_qbt_offload_status(
                last_run=last_run,
                offloaded=offloaded,
                failed=failed,
                hdd_full=hdd_full,
                pending_on_nvme=pending_on_nvme,
            ),
        )


def _run_candidates(
    candidates: list[dict[str, Any]],
    *,
    pending_count: int,
    hdd_mount: str | None = None,
) -> int:
    started_at = int(time.time())
    with _OFFLOAD_STATUS_LOCK:
        try:
            with _qbt_client() as qbt:
                results = run_offload_moves(
                    qbt,
                    candidates,
                    min_hdd_free_bytes=_env_int("QBT_OFFLOAD_MIN_HDD_FREE_BYTES", 10_737_418_240),
                    free_space_mount=hdd_mount or _paths()[1],
                )
        except Exception as exc:
            _write_status(
                last_run=started_at,
                offloaded=0,
                failed=1,
                hdd_full=False,
                pending_on_nvme=pending_count,
            )
            print(f"homelab-qbt-offload: ERROR: {exc}", file=sys.stderr)
            return 1

        offloaded = sum(1 for result in results if result.get("ok"))
        hdd_full = any(result.get("blocked_reason") == "hdd_full" for result in results)
        failed = sum(
            1 for result in results if not result.get("ok") and result.get("blocked_reason") != "hdd_full"
        )
        _write_status(
            last_run=started_at,
            offloaded=offloaded,
            failed=failed,
            hdd_full=hdd_full,
            pending_on_nvme=max(0, pending_count - offloaded),
        )
        print(
            "homelab-qbt-offload: "
            f"offloaded={offloaded} failed={failed} hdd_full={hdd_full} pending={pending_count - offloaded}"
        )
        return 1 if failed else 0


def run_catch_up(*, limit: int = 20) -> int:
    nvme_mount, hdd_mount = _paths()
    with _OFFLOAD_STATUS_LOCK:
        try:
            excluded_tags, excluded_categories = _offload_exclusions()
            with _qbt_client() as qbt:
                torrents = qbt.torrents_info_raw()
            all_candidates = list_offload_candidates(
                torrents,
                nvme_mount=nvme_mount,
                hdd_mount=hdd_mount,
                limit=len(torrents),
                excluded_tags=excluded_tags,
                excluded_categories=excluded_categories,
            )
        except Exception as exc:
            started_at = int(time.time())
            _write_status(
                last_run=started_at,
                offloaded=0,
                failed=1,
                hdd_full=False,
                pending_on_nvme=0,
            )
            print(f"homelab-qbt-offload: ERROR: {exc}", file=sys.stderr)
            return 1
        return _run_candidates(
            all_candidates[:limit],
            pending_count=len(all_candidates),
            hdd_mount=hdd_mount,
        )


def run_offload_hash(hash_value: str) -> int:
    nvme_mount, hdd_mount = _paths()
    started_at = int(time.time())
    try:
        excluded_tags, excluded_categories = _offload_exclusions()
        with _qbt_client() as qbt:
            torrents = qbt.torrents_info_raw()
        torrent = next(
            (row for row in torrents if str(row.get("hash") or "").lower() == hash_value.lower()),
            None,
        )
        candidate = (
            plan_offload_candidate(
                torrent,
                nvme_mount=nvme_mount,
                hdd_mount=hdd_mount,
                excluded_tags=excluded_tags,
                excluded_categories=excluded_categories,
            )
            if torrent is not None
            else None
        )
    except Exception as exc:
        _write_status(
            last_run=started_at,
            offloaded=0,
            failed=1,
            hdd_full=False,
            pending_on_nvme=0,
        )
        print(f"homelab-qbt-offload: ERROR: {exc}", file=sys.stderr)
        return 1
    if torrent is None:
        _write_status(
            last_run=started_at,
            offloaded=0,
            failed=1,
            hdd_full=False,
            pending_on_nvme=0,
        )
        print(f"homelab-qbt-offload: torrent not found: {hash_value}", file=sys.stderr)
        return 1
    if candidate is None:
        _write_status(
            last_run=started_at,
            offloaded=0,
            failed=0,
            hdd_full=False,
            pending_on_nvme=0,
        )
        print(f"homelab-qbt-offload: already offloaded or not on NVMe: {hash_value}")
        return 0
    return _run_candidates([candidate], pending_count=1, hdd_mount=hdd_mount)


def health_payload() -> tuple[int, dict[str, Any]]:
    health = evaluate_qbt_offload_health(
        read_qbt_offload_status(_status_path()),
        max_age_seconds=_env_int("QBT_OFFLOAD_STATUS_MAX_AGE_SECONDS", 7200),
    )
    return (200 if health["healthy"] else 503), health


def healthcheck() -> int:
    status, health = health_payload()
    stream = sys.stdout if status == 200 else sys.stderr
    print(f"homelab-qbt-offload: {health['reason']}", file=stream)
    return 0 if status == 200 else 1


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/healthz":
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            status, body = health_payload()
            self._send_json(status, body)

        def do_POST(self) -> None:
            if self.path != "/v1/offload":
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                hash_value = body.get("hash") if isinstance(body, dict) else None
                if not isinstance(hash_value, str) or not hash_value.strip():
                    raise ValueError("hash is required")
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            with _OFFLOAD_STATUS_LOCK:
                exit_code = run_offload_hash(hash_value)
                _, health = health_payload()
                if health["reason"] == "hdd_full":
                    self._send_json(503, {"ok": False, "error": "hdd_full", "blocked": True})
                    return
            self._send_json(200 if exit_code == 0 else 503, {"ok": exit_code == 0})

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


def run_loop(*, limit: int) -> None:
    interval_seconds = _env_int("QBT_OFFLOAD_CATCHUP_SECONDS", 3600)
    while True:
        run_catch_up(limit=limit)
        time.sleep(interval_seconds)


def serve(*, host: str, port: int, limit: int) -> None:
    threading.Thread(target=run_loop, kwargs={"limit": limit}, daemon=True).start()
    make_server(host, port).serve_forever()


def main(argv: list[str] | None = None) -> int:
    bootstrap_container_secrets()
    parser = argparse.ArgumentParser(description="Offload completed qBittorrent torrents to HDD")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--hash", metavar="HASH", help="Offload one torrent")
    actions.add_argument("--catch-up", action="store_true", help="Offload a batch from NVMe")
    actions.add_argument("--serve", action="store_true", help="Serve the completion hook")
    actions.add_argument("--healthcheck", action="store_true", help="Check offload status")
    actions.add_argument("--once", action="store_true", help="Run one catch-up cycle and exit")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args(argv)

    if args.healthcheck:
        return healthcheck()
    if args.hash:
        return run_offload_hash(args.hash)
    if args.catch_up or args.once:
        return run_catch_up(limit=args.limit)
    serve(host=args.host, port=args.port, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
