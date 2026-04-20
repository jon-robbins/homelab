from __future__ import annotations

import sys

from .args import parse_args
from .processors import process_radarr, process_sonarr


def main() -> int:
    args = parse_args()
    dry_run = not args.apply
    print(f"Mode: {'dry-run' if dry_run else 'apply'}")
    print(f"search_missing_monitored={args.search_missing_monitored} min_seeders={args.min_seeders}")

    total_retries = 0
    try:
        total_retries += process_sonarr(args, dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"[sonarr][error] {exc}", file=sys.stderr)
        return 1

    try:
        total_retries += process_radarr(args, dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"[radarr][error] {exc}", file=sys.stderr)
        return 1

    print(f"Total retries planned={total_retries}")
    if dry_run:
        print("Dry-run complete. Re-run with --apply to execute retries.")
    else:
        print("Apply run complete.")
    return 0
