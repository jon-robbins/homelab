#!/usr/bin/env python3
"""
Point Seerr (overseerr) Radarr/Sonarr server entries at Docker DNS names + UrlBase.

Seerr runs on homelab_net; 127.0.0.1 inside the container is not Sonarr/Radarr.
Match UrlBase from each app's config.xml (defaults: /radarr, /sonarr).

Usage:
  python3 scripts/migrate-seerr-arr-hosts.py
  python3 scripts/migrate-seerr-arr-hosts.py --dry-run
  docker compose -f docker-compose.media.yml restart overseerr
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


def _url_base_from_xml(config_xml: Path) -> str:
    if not config_xml.is_file():
        return ""
    try:
        root = ET.parse(config_xml).getroot()
        el = root.find("UrlBase")
        if el is not None and el.text and str(el.text).strip():
            return str(el.text).strip()
    except ET.ParseError as exc:
        print(f"[warn] could not parse {config_xml}: {exc}", file=sys.stderr)
    return ""


def _patch_servers(
    servers: list[object],
    new_host: str,
    new_base: str,
    dry_run: bool,
    label: str,
) -> bool:
    if not isinstance(servers, list):
        return False
    changed = False
    for item in servers:
        if not isinstance(item, dict):
            continue
        old_host = str(item.get("hostname") or "")
        if old_host not in ("127.0.0.1", "localhost", "::1"):
            continue
        old_base = str(item.get("baseUrl") or "")
        print(
            f"[plan] {label} {item.get('name')!r}: "
            f"hostname {old_host!r} -> {new_host!r}, baseUrl {old_base!r} -> {new_base!r}"
        )
        if not dry_run:
            item["hostname"] = new_host
            item["baseUrl"] = new_base
        changed = True
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--settings",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "overseerr" / "settings.json",
        help="Path to Seerr settings.json",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data",
        help="Directory containing sonarr/, radarr/ config.xml",
    )
    parser.add_argument("--radarr-host", default="radarr")
    parser.add_argument("--sonarr-host", default="sonarr")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rad_base = _url_base_from_xml(args.data_dir / "radarr" / "config.xml") or "/radarr"
    son_base = _url_base_from_xml(args.data_dir / "sonarr" / "config.xml") or "/sonarr"

    if not args.settings.is_file():
        print(f"[error] missing {args.settings}", file=sys.stderr)
        return 1

    text = args.settings.read_text(encoding="utf-8")
    cfg = json.loads(text)

    radarr = cfg.get("radarr")
    sonarr = cfg.get("sonarr")
    c1 = _patch_servers(radarr, args.radarr_host, rad_base, args.dry_run, "radarr")
    c2 = _patch_servers(sonarr, args.sonarr_host, son_base, args.dry_run, "sonarr")

    if not c1 and not c2:
        print("No Radarr/Sonarr entries pointing at localhost/127.0.0.1 (nothing to do).")
        return 0

    if args.dry_run:
        print("Dry run only; run again without --dry-run to write settings.json")
        return 0

    backup = args.settings.with_suffix(".json.bak-migrate-arr")
    shutil.copy2(args.settings, backup)
    args.settings.write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {args.settings} (backup: {backup})")
    print("Restart Seerr: docker compose -f docker-compose.media.yml restart overseerr")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
