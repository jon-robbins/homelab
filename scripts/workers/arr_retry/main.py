from __future__ import annotations

import sys

from .args import parse_args
from .health import HealthStateStore
from .processors import process_radarr, process_sonarr


def main() -> int:
    args = parse_args()
    dry_run = not args.apply
    print(f"Mode: {'dry-run' if dry_run else 'apply'}")
    print(f"search_missing_monitored={args.search_missing_monitored} min_seeders={args.min_seeders}")

    total_retries = 0
    total_health_actions = 0
    health_actions_remaining = max(0, args.health_max_actions_per_sweep)
    state_store = HealthStateStore.load(args.health_state_file)
    try:
        sonarr_result = process_sonarr(args, dry_run, health_actions_remaining, state_store)
        total_retries += sonarr_result.retries_planned
        total_health_actions += sonarr_result.health_actions_used
        health_actions_remaining = max(0, health_actions_remaining - sonarr_result.health_actions_used)
    except Exception as exc:  # noqa: BLE001
        print(f"[sonarr][error] {exc}", file=sys.stderr)
        return 1

    try:
        radarr_result = process_radarr(args, dry_run, health_actions_remaining, state_store)
        total_retries += radarr_result.retries_planned
        total_health_actions += radarr_result.health_actions_used
        health_actions_remaining = max(0, health_actions_remaining - radarr_result.health_actions_used)
    except Exception as exc:  # noqa: BLE001
        print(f"[radarr][error] {exc}", file=sys.stderr)
        return 1

    if not dry_run:
        state_store.save(args.health_state_file)

    print(f"Total retries planned={total_retries}")
    if args.enable_health_replacement or args.enable_health_race:
        print(f"Total health actions planned={total_health_actions}")
    if dry_run:
        print("Dry-run complete. Re-run with --apply to execute retries.")
    else:
        print("Apply run complete.")
    return 0
