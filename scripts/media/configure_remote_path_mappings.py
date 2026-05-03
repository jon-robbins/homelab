#!/usr/bin/env python3
"""Configure Remote Path Mappings for Sonarr and Radarr via the v3 API.

This script creates remote path mappings that tell Sonarr/Radarr how to
translate file paths reported by a remote download client (seedbox) into
paths that are accessible on the local server.

Usage:
    python3 configure_remote_path_mappings.py           # apply mappings
    python3 configure_remote_path_mappings.py --dry-run  # preview only

Before running, verify:
  - The SEEDBOX_HOST value exactly matches the "Host" field configured in
    your Sonarr/Radarr Download Client settings.
  - The qBittorrent categories match: this script assumes "tv-sonarr" for
    Sonarr and "radarr" for Radarr. If you used "tv" and "movies" instead,
    update SONARR_REMOTE_PATH and RADARR_REMOTE_PATH accordingly.
  - All local paths end with a trailing slash.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests

# ── Configuration ────────────────────────────────────────────────────────────
# Service URLs (from the Docker host).  Both apps have a URL base configured.
SONARR_URL = os.environ.get("SONARR_URL", "http://127.0.0.1:8989/sonarr")
RADARR_URL = os.environ.get("RADARR_URL", "http://127.0.0.1:7878/radarr")

# API keys – env-var takes precedence over the hard-coded defaults.
SONARR_API_KEY = os.environ.get("SONARR_API_KEY", "7178f826d99148eb9f62878bf8d93e32")
RADARR_API_KEY = os.environ.get("RADARR_API_KEY", "a8a686778fc5438d8f0f1fd0799cdb35")

# Seedbox download-client host.  Must *exactly* match the Host field of the
# download client configured in Sonarr/Radarr.
SEEDBOX_HOST = "33.nl137.seedit4.me"

# Remote paths on the seedbox (where qBittorrent stores completed downloads).
SONARR_REMOTE_PATH = "/home/seedit4me/torrents/qbittorrent/tv-sonarr/"
RADARR_REMOTE_PATH = "/home/seedit4me/torrents/qbittorrent/radarr/"

# Local paths where the seedbox files are accessible *inside* the containers.
# Because qBittorrent, Sonarr, and Radarr all share the same host directory
# (/mnt/media-nvme/Incoming) mounted as /downloads, the local path references
# the container-internal mount.
#
# If your seedbox syncs to a different location, update these paths accordingly.
# All paths MUST end with a trailing slash.
SONARR_LOCAL_PATH = "/downloads/tv-sonarr/"
RADARR_LOCAL_PATH = "/downloads/radarr/"

# HTTP timeout for API calls (seconds).
HTTP_TIMEOUT = 30
# ── End Configuration ────────────────────────────────────────────────────────


def _headers(api_key: str) -> dict[str, str]:
    """Return common headers for *arr API requests."""
    return {
        "X-Api-Key": api_key,
        "Content-Type": "application/json",
    }


def get_existing_mappings(
    base_url: str, api_key: str, label: str
) -> list[dict] | None:
    """Fetch and display current remote path mappings. Returns the list or None on error."""
    url = f"{base_url}/api/v3/remotepathmapping"
    print(f"\n{'─' * 60}")
    print(f"  {label} – existing remote path mappings")
    print(f"  GET {url}")
    print(f"{'─' * 60}")
    try:
        resp = requests.get(url, headers=_headers(api_key), timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
    except requests.ConnectionError:
        print(f"  ERROR: could not connect to {label} at {base_url}")
        return None
    except requests.HTTPError as exc:
        print(f"  ERROR: HTTP {resp.status_code} – {exc}")
        print(f"  Response: {resp.text}")
        return None

    mappings = resp.json()
    if not mappings:
        print("  (none)")
    else:
        for m in mappings:
            print(
                f"  id={m.get('id')}  host={m.get('host')!r}  "
                f"remote={m.get('remotePath')!r}  local={m.get('localPath')!r}"
            )
    return mappings


def mapping_exists(
    mappings: list[dict], host: str, remote_path: str
) -> bool:
    """Check whether a mapping for the given host+remotePath already exists."""
    for m in mappings:
        if m.get("host") == host and m.get("remotePath") == remote_path:
            return True
    return False


def create_mapping(
    base_url: str,
    api_key: str,
    label: str,
    host: str,
    remote_path: str,
    local_path: str,
    *,
    dry_run: bool = False,
) -> bool:
    """POST a new remote path mapping. Returns True on success."""
    url = f"{base_url}/api/v3/remotepathmapping"
    payload = {
        "host": host,
        "remotePath": remote_path,
        "localPath": local_path,
    }

    print(f"\n  {label} – creating remote path mapping")
    print(f"  POST {url}")
    print(f"  Payload: {json.dumps(payload, indent=4)}")

    if dry_run:
        print("  ** DRY RUN – skipping actual request **")
        return True

    try:
        resp = requests.post(
            url,
            headers=_headers(api_key),
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
    except requests.ConnectionError:
        print(f"  ERROR: could not connect to {label} at {base_url}")
        return False

    if resp.ok:
        created = resp.json()
        print(f"  SUCCESS (HTTP {resp.status_code})")
        print(
            f"  Created mapping id={created.get('id')}  "
            f"host={created.get('host')!r}  "
            f"remote={created.get('remotePath')!r}  "
            f"local={created.get('localPath')!r}"
        )
        return True

    print(f"  FAILED (HTTP {resp.status_code})")
    print(f"  Response: {resp.text}")
    return False


def configure_app(
    base_url: str,
    api_key: str,
    label: str,
    remote_path: str,
    local_path: str,
    *,
    dry_run: bool = False,
) -> bool:
    """Full workflow for one *arr app: fetch existing, check dupes, create."""
    mappings = get_existing_mappings(base_url, api_key, label)
    if mappings is None:
        if not dry_run:
            return False
        print("  (could not fetch existing mappings — skipping duplicate check in dry-run mode)")

    if mappings is not None and mapping_exists(mappings, SEEDBOX_HOST, remote_path):
        print(
            f"\n  {label} – mapping for host={SEEDBOX_HOST!r} "
            f"remote={remote_path!r} already exists. Skipping."
        )
        return True

    return create_mapping(
        base_url, api_key, label, SEEDBOX_HOST, remote_path, local_path,
        dry_run=dry_run,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Configure remote path mappings for Sonarr and Radarr."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be sent without making any changes.",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("*** DRY-RUN MODE – no changes will be made ***\n")

    ok = True

    # Sonarr
    if not configure_app(
        SONARR_URL, SONARR_API_KEY, "Sonarr",
        SONARR_REMOTE_PATH, SONARR_LOCAL_PATH,
        dry_run=args.dry_run,
    ):
        ok = False

    # Radarr
    if not configure_app(
        RADARR_URL, RADARR_API_KEY, "Radarr",
        RADARR_REMOTE_PATH, RADARR_LOCAL_PATH,
        dry_run=args.dry_run,
    ):
        ok = False

    print()
    if ok:
        print("All mappings configured successfully.")
    else:
        print("One or more mappings failed – see errors above.", file=sys.stderr)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
