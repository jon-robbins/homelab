from __future__ import annotations

import logging
import sys

from .args import parse_args
from .health import HealthStateStore
from .processors import process_radarr, process_sonarr


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    log = logging.getLogger("arr_retry")
    args = parse_args()
    dry_run = not args.apply
    log.info("Mode: %s", "dry-run" if dry_run else "apply")
    log.info("search_missing_monitored=%s min_seeders=%s", args.search_missing_monitored, args.min_seeders)

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
        log.exception("[sonarr][error] %s", exc)
        return 1

    try:
        radarr_result = process_radarr(args, dry_run, health_actions_remaining, state_store)
        total_retries += radarr_result.retries_planned
        total_health_actions += radarr_result.health_actions_used
        health_actions_remaining = max(0, health_actions_remaining - radarr_result.health_actions_used)
    except Exception as exc:  # noqa: BLE001
        log.exception("[radarr][error] %s", exc)
        return 1

    if not dry_run:
        state_store.save(args.health_state_file)

    log.info("Total retries planned=%s", total_retries)
    if args.enable_health_replacement or args.enable_health_race:
        log.info("Total health actions planned=%s", total_health_actions)
    if dry_run:
        log.info("Dry-run complete. Re-run with --apply to execute retries.")
    else:
        log.info("Apply run complete.")
    return 0
