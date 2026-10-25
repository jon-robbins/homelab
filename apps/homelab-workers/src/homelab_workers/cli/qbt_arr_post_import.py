from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from homelab_workers.shared.arr import ArrClient, read_api_key_from_config_xml
from homelab_workers.shared.arr_post_import import (
    apply_post_import_relocate,
    container_to_host_path,
    plan_post_import_relocate,
)
from homelab_workers.shared.qbittorrent import QBittorrentClient
from homelab_workers.shared.secrets import bootstrap_container_secrets

# Secrets required when this CLI runs via docker exec into MCP (Compose
# mounts files under /run/secrets; container env no longer carries them).
_ARR_POST_IMPORT_SECRETS: tuple[str, ...] = (
    "SONARR_API_KEY",
    "RADARR_API_KEY",
    "QBITTORRENT_USERNAME",
    "QBITTORRENT_PASSWORD",
)


def _load_state(path: Path) -> set[str]:

    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return set()
    rows = payload.get("processed_download_ids")
    if not isinstance(rows, list):
        return set()
    return {str(item).lower() for item in rows if str(item).strip()}


def _save_state(path: Path, processed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"processed_download_ids": sorted(processed)}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _fetch_history(client: ArrClient, *, page_size: int = 100) -> list[dict[str, Any]]:
    payload = client.get(
        "/history",
        query={"page": 1, "pageSize": page_size, "sortKey": "date", "sortDirection": "descending"},
    )
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return []
    return [row for row in records if isinstance(row, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Relocate arr-imported torrents from NVMe staging to library paths.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--history-limit", type=int, default=100)
    args = parser.parse_args()

    # Prefer /run/secrets when exec'd into MCP (no Compose environment secrets).
    bootstrap_container_secrets(_ARR_POST_IMPORT_SECRETS)

    sonarr_url = os.environ.get("SONARR_INTERNAL_URL", "http://sonarr:8989/sonarr")
    radarr_url = os.environ.get("RADARR_INTERNAL_URL", "http://radarr:7878/radarr")
    sonarr_key = os.environ.get("SONARR_API_KEY") or read_api_key_from_config_xml("data/sonarr/config.xml")
    radarr_key = os.environ.get("RADARR_API_KEY") or read_api_key_from_config_xml("data/radarr/config.xml")
    qbt_url = os.environ.get("QBITTORRENT_INTERNAL_URL", os.environ.get("QBITTORRENT_URL", "http://qbittorrent:8080"))
    qbt_user = os.environ.get("QBITTORRENT_USERNAME", "")
    qbt_pass = os.environ.get("QBITTORRENT_PASSWORD", "")
    state_path = Path(os.environ.get("ARR_POST_IMPORT_STATE_PATH", "data/qbittorrent/arr-post-import-state.json"))

    processed = _load_state(state_path)
    history_rows: list[dict[str, Any]] = []

    try:
        with ArrClient(sonarr_url, sonarr_key) as sonarr, ArrClient(radarr_url, radarr_key) as radarr, QBittorrentClient(qbt_url, qbt_user, qbt_pass) as qbt:
            history_rows.extend(_fetch_history(sonarr, page_size=args.history_limit))
            history_rows.extend(_fetch_history(radarr, page_size=args.history_limit))
            torrents = qbt.torrents_info_raw()
            actions = plan_post_import_relocate(
                history_rows=history_rows,
                torrents=torrents,
                processed_ids=processed,
                nvme_downloads_prefix="/downloads",
                library_prefixes=("/tv", "/movies"),
            )
            enriched: list[dict[str, Any]] = []
            downloads_host = Path(os.environ.get("MEDIA_NVME_PATH", "/mnt/media-nvme")) / "Incoming"
            tv_host = Path(os.environ.get("MEDIA_HDD_PATH", "/mnt/media-hdd")) / "TV"
            movies_host = Path(os.environ.get("MEDIA_HDD_PATH", "/mnt/media-hdd")) / "Movies"
            for action in actions:
                row = dict(action)
                row["library_file_host"] = str(
                    container_to_host_path(
                        str(action["library_file"]),
                        downloads_container="/downloads",
                        downloads_host=downloads_host,
                        tv_container="/tv",
                        tv_host=tv_host,
                        movies_container="/movies",
                        movies_host=movies_host,
                    )
                )
                row["staging_host"] = str(
                    container_to_host_path(
                        str(action["staging_path"]),
                        downloads_container="/downloads",
                        downloads_host=downloads_host,
                        tv_container="/tv",
                        tv_host=tv_host,
                        movies_container="/movies",
                        movies_host=movies_host,
                    )
                )
                enriched.append(row)
            results = []
            for action in enriched:
                path_map = {
                    "library_file_host": Path(str(action["library_file_host"])),
                    "staging_host": Path(str(action["staging_host"])),
                }
                results.extend(apply_post_import_relocate(qbt, [action], dry_run=args.dry_run, path_map=path_map))
    except Exception as exc:
        print(f"qbt-arr-post-import: ERROR: {exc}", file=sys.stderr)
        return 1

    ok_count = sum(1 for row in results if row.get("ok"))
    print(f"qbt-arr-post-import: planned={len(results)} ok={ok_count} dry_run={args.dry_run}")
    for row in results:
        if not row.get("ok"):
            print(f"qbt-arr-post-import: FAIL hash={row.get('hash')} error={row.get('error')}", file=sys.stderr)
        elif row.get("skipped"):
            print(f"qbt-arr-post-import: KEEP hash={row.get('hash')} seeding from staging: {row['skipped']}")
        elif not args.dry_run and row.get("hash"):
            processed.add(str(row["hash"]).lower())

    if not args.dry_run:
        _save_state(state_path, processed)
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
