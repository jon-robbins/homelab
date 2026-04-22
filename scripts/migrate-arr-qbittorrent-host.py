#!/usr/bin/env python3
"""
Point *arr Download Client (qBittorrent) Settings at the Docker service hostname.

After moving Sonarr/Radarr/Readarr to bridge networking, localhost/127.0.0.1:8080
refers to the *arr container itself, not qBittorrent — Radarr shows
"Connection refused (localhost:8080)".

Usage (from repo root, containers stopped recommended):
  python3 scripts/migrate-arr-qbittorrent-host.py --host qbittorrent
  docker compose -f docker-compose.media.yml up -d radarr sonarr readarr

Dry run:
  python3 scripts/migrate-arr-qbittorrent-host.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _patch_db(db_path: Path, new_host: str, dry_run: bool) -> bool:
    if not db_path.is_file():
        return False
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT Id, Name, Implementation, Settings FROM DownloadClients "
            "WHERE Implementation = 'QBittorrent'"
        )
        rows = cur.fetchall()
        changed = False
        for dc_id, name, impl, settings_raw in rows:
            try:
                cfg = json.loads(settings_raw or "{}")
            except json.JSONDecodeError:
                print(f"[skip] {db_path}: client id={dc_id} name={name!r} invalid JSON", file=sys.stderr)
                continue
            old = cfg.get("host")
            if old == new_host:
                print(f"[ok]   {db_path.name}: {name!r} already host={new_host!r}")
                continue
            cfg["host"] = new_host
            new_json = json.dumps(cfg, separators=(",", ":"))
            print(f"[plan] {db_path.name}: {name!r} host {old!r} -> {new_host!r}")
            changed = True
            if not dry_run:
                cur.execute(
                    "UPDATE DownloadClients SET Settings = ? WHERE Id = ?",
                    (new_json, dc_id),
                )
        if changed and not dry_run:
            conn.commit()
        return changed
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data",
        help="Directory containing sonarr/, radarr/, readarr/ SQLite databases",
    )
    parser.add_argument(
        "--host",
        default="qbittorrent",
        help="Hostname or Docker service name for qBittorrent WebUI (default: qbittorrent)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes only")
    args = parser.parse_args()

    bases = [
        ("radarr", "radarr.db"),
        ("sonarr", "sonarr.db"),
        ("readarr", "readarr.db"),
    ]
    any_changed = False
    for sub, dbname in bases:
        p = args.data_dir / sub / dbname
        if _patch_db(p, args.host, args.dry_run):
            any_changed = True
    if args.dry_run:
        if any_changed:
            print("Dry run only; run again without --dry-run to write changes.")
        else:
            print("No changes needed (or no QBittorrent clients found).")
    else:
        print("Done. Restart *arr containers if they were running during the update.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
