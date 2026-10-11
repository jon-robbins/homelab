from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.arr_missing_search import (
    parse_radarr_missing_records,
    parse_sonarr_missing_records,
    seconds_until_next_run,
    select_missing_batch,
)
from homelab_workers.shared.arr_missing_search_state import (
    arr_missing_search_lock,
    build_missing_search_status,
    evaluate_missing_search_health,
    read_missing_search_status,
    write_missing_search_status,
)
from homelab_workers.shared.config import (
    get_config_bool,
    get_config_int,
    load_worker_yaml_config,
)
from homelab_workers.shared.secrets import bootstrap_container_secrets

PAGE_SIZE = 100
MAX_PAGES = 50
COMMAND_CHUNK_SIZE = 30
MAX_ITEMS_PER_RUN = 50
SECRET_QUERY_PATTERN = re.compile(
    r"(?i)\b(api[-_]?key|access[-_]?token|token)=([^&\s'\"<>]+)"
)


def _get_yaml() -> dict[str, Any]:
    return load_worker_yaml_config().get("arr_missing_search", {})


def _env_bool(name: str, default: bool) -> bool:
    yaml_key = name.replace("ARR_MISSING_SEARCH_", "").lower()
    return get_config_bool(os.environ, _get_yaml(), name, yaml_key, default)


def _env_int(name: str, default: int) -> int:
    yaml_key = name.replace("ARR_MISSING_SEARCH_", "").lower()
    return get_config_int(os.environ, _get_yaml(), name, yaml_key, default)


def _next_scheduled_at(now: int) -> int:
    timezone = ZoneInfo(os.environ.get("TZ", "UTC"))
    local_now = datetime.fromtimestamp(now, tz=timezone)
    delay = seconds_until_next_run(
        now=local_now,
        hour=_env_int("ARR_MISSING_SEARCH_HOUR", 4),
        minute=_env_int("ARR_MISSING_SEARCH_MINUTE", 15),
    )
    return now + delay


def _paged_records(client: ArrClient, path: str, *, queue: bool = False) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        query: dict[str, object] = {"page": page, "pageSize": PAGE_SIZE}
        if queue:
            query["includeUnknownMovieItems"] = True
        payload = client.get(path, query=query)
        if not isinstance(payload, dict):
            raise TypeError(f"{path} response must be an object")
        page_records = payload.get("records")
        if not isinstance(page_records, list):
            raise TypeError(f"{path} records must be a list")
        records.extend(row for row in page_records if isinstance(row, dict))
        try:
            total_records = int(payload.get("totalRecords"))
        except (TypeError, ValueError):
            total_records = 0
        if not page_records or (total_records > 0 and len(records) >= total_records):
            break
    return records


def _queue_ids(records: list[dict[str, Any]], direct_key: str, nested_key: str) -> set[int]:
    ids: set[int] = set()
    for record in records:
        value = record.get(direct_key)
        nested = record.get(nested_key)
        if value is None and isinstance(nested, dict):
            value = nested.get("id")
        try:
            if value is not None:
                ids.add(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def _sanitized_error(exc: Exception) -> str:
    message = SECRET_QUERY_PATTERN.sub(r"\1=[REDACTED]", str(exc))
    return f"{type(exc).__name__}: {message[:300]}"


def _post_commands(
    client: ArrClient, name: str, ids_key: str, ids: list[int]
) -> Iterator[int]:
    for offset in range(0, len(ids), COMMAND_CHUNK_SIZE):
        chunk = ids[offset : offset + COMMAND_CHUNK_SIZE]
        client.post(
            "/command",
            {"name": name, ids_key: chunk},
        )
        yield len(chunk)


def _run_once_cycle(*, dry_run: bool) -> int:
    enabled = _env_bool("ARR_MISSING_SEARCH_ENABLED", False)
    status_path = os.environ.get("ARR_MISSING_SEARCH_STATUS_PATH", "/state/status.json")
    started_at = int(time.time())

    if not enabled:
        write_missing_search_status(
            status_path,
            build_missing_search_status(
                enabled=False,
                running=False,
                ok=True,
                dry_run=dry_run,
                started_at=started_at,
                completed_at=started_at,
                next_scheduled_at=None,
                sonarr_missing_total=0,
                radarr_missing_total=0,
                sonarr_searched=0,
                radarr_searched=0,
                consecutive_failures=0,
            ),
        )
        print("homelab-arr-missing-search: disabled")
        return 0

    lock_path = os.environ.get("ARR_MISSING_SEARCH_LOCK_PATH", "/state/search.lock")
    previous = read_missing_search_status(status_path) or {}
    consecutive_failures = int(previous.get("consecutive_failures") or 0)
    _next_scheduled_at(started_at)

    sonarr_missing_total = 0
    radarr_missing_total = 0
    sonarr_searched = 0
    radarr_searched = 0
    try:
        with arr_missing_search_lock(lock_path):
            with (
                ArrClient(
                    os.environ.get("SONARR_INTERNAL_URL", "http://sonarr:8989/sonarr"),
                    os.environ.get("SONARR_API_KEY", ""),
                ) as sonarr,
                ArrClient(
                    os.environ.get("RADARR_INTERNAL_URL", "http://radarr:7878/radarr"),
                    os.environ.get("RADARR_API_KEY", ""),
                ) as radarr,
            ):
                errors: list[str] = []
                sonarr_items = []
                radarr_items = []
                try:
                    sonarr_missing = _paged_records(sonarr, "/wanted/missing")
                    sonarr_queue = _paged_records(sonarr, "/queue", queue=True)
                    sonarr_missing_total = len(sonarr_missing)
                    sonarr_items = parse_sonarr_missing_records(
                        sonarr_missing,
                        _queue_ids(sonarr_queue, "episodeId", "episode"),
                    )
                except Exception as exc:
                    errors.append(f"sonarr {_sanitized_error(exc)}")
                try:
                    radarr_missing = _paged_records(radarr, "/wanted/missing")
                    radarr_queue = _paged_records(radarr, "/queue", queue=True)
                    radarr_missing_total = len(radarr_missing)
                    radarr_items = parse_radarr_missing_records(
                        radarr_missing,
                        _queue_ids(radarr_queue, "movieId", "movie"),
                    )
                except Exception as exc:
                    errors.append(f"radarr {_sanitized_error(exc)}")

                cap = min(max(_env_int("ARR_MISSING_SEARCH_CAP", 50), 0), MAX_ITEMS_PER_RUN)
                sonarr_quota = min(max(_env_int("ARR_MISSING_SEARCH_SONARR_QUOTA", 25), 0), cap)
                radarr_quota = min(max(_env_int("ARR_MISSING_SEARCH_RADARR_QUOTA", 25), 0), cap)
                selected = select_missing_batch(
                    sonarr_items,
                    radarr_items,
                    cap=cap,
                    sonarr_quota=sonarr_quota,
                    radarr_quota=radarr_quota,
                )
                sonarr_ids = [item.item_id for item in selected if item.app == "sonarr"]
                radarr_ids = [item.item_id for item in selected if item.app == "radarr"]
                if dry_run:
                    sonarr_searched = len(sonarr_ids)
                    radarr_searched = len(radarr_ids)
                else:
                    try:
                        for posted in _post_commands(
                            sonarr, "EpisodeSearch", "episodeIds", sonarr_ids
                        ):
                            sonarr_searched += posted
                    except Exception as exc:
                        errors.append(f"sonarr {_sanitized_error(exc)}")
                    try:
                        for posted in _post_commands(
                            radarr, "MoviesSearch", "movieIds", radarr_ids
                        ):
                            radarr_searched += posted
                    except Exception as exc:
                        errors.append(f"radarr {_sanitized_error(exc)}")

        completed_at = int(time.time())
        ok = not errors or sonarr_searched + radarr_searched > 0
        write_missing_search_status(
            status_path,
            build_missing_search_status(
                enabled=True,
                running=False,
                ok=ok,
                dry_run=dry_run,
                started_at=started_at,
                completed_at=completed_at,
                next_scheduled_at=_next_scheduled_at(completed_at),
                sonarr_missing_total=sonarr_missing_total,
                radarr_missing_total=radarr_missing_total,
                sonarr_searched=sonarr_searched,
                radarr_searched=radarr_searched,
                consecutive_failures=0 if ok else consecutive_failures + 1,
                last_error="; ".join(errors) if errors else None,
            ),
        )
        print(
            "homelab-arr-missing-search: "
            f"dry_run={dry_run} "
            f"sonarr_missing={sonarr_missing_total} "
            f"radarr_missing={radarr_missing_total} "
            f"sonarr_searched={sonarr_searched} "
            f"radarr_searched={radarr_searched}"
        )
        return 0 if ok else 1
    except Exception as exc:
        completed_at = int(time.time())
        try:
            write_missing_search_status(
                status_path,
                build_missing_search_status(
                    enabled=True,
                    running=False,
                    ok=False,
                    dry_run=dry_run,
                    started_at=started_at,
                    completed_at=completed_at,
                    next_scheduled_at=_next_scheduled_at(completed_at),
                    sonarr_missing_total=sonarr_missing_total,
                    radarr_missing_total=radarr_missing_total,
                    sonarr_searched=sonarr_searched,
                    radarr_searched=radarr_searched,
                    consecutive_failures=consecutive_failures + 1,
                    last_error=_sanitized_error(exc),
                ),
            )
        except Exception:
            pass
        print(f"homelab-arr-missing-search: ERROR: {exc}", file=sys.stderr)
        return 1


def run_once(*, dry_run: bool) -> int:
    try:
        return _run_once_cycle(dry_run=dry_run)
    except Exception as exc:
        status_path = os.environ.get("ARR_MISSING_SEARCH_STATUS_PATH", "/state/status.json")
        completed_at = started_at = int(time.time())
        consecutive_failures = 0
        try:
            previous = read_missing_search_status(status_path) or {}
            consecutive_failures = int(previous.get("consecutive_failures") or 0)
        except Exception:
            pass
        try:
            next_scheduled_at = _next_scheduled_at(completed_at)
        except Exception:
            next_scheduled_at = None
        try:
            write_missing_search_status(
                status_path,
                build_missing_search_status(
                    enabled=_env_bool("ARR_MISSING_SEARCH_ENABLED", False),
                    running=False,
                    ok=False,
                    dry_run=dry_run,
                    started_at=started_at,
                    completed_at=completed_at,
                    next_scheduled_at=next_scheduled_at,
                    sonarr_missing_total=0,
                    radarr_missing_total=0,
                    sonarr_searched=0,
                    radarr_searched=0,
                    consecutive_failures=consecutive_failures + 1,
                    last_error=_sanitized_error(exc),
                ),
            )
        except Exception:
            pass
        print(f"homelab-arr-missing-search: ERROR: {exc}", file=sys.stderr)
        return 1


def healthcheck() -> int:
    status_path = os.environ.get("ARR_MISSING_SEARCH_STATUS_PATH", "/state/status.json")
    health = evaluate_missing_search_health(
        read_missing_search_status(status_path),
        max_age_seconds=_env_int("ARR_MISSING_SEARCH_STATUS_MAX_AGE_SECONDS", 90000),
    )
    if health["healthy"]:
        print(f"homelab-arr-missing-search: healthy ({health['reason']})")
        return 0
    print(f"homelab-arr-missing-search: unhealthy ({health['reason']})", file=sys.stderr)
    return 1


def run_loop(*, dry_run: bool) -> int:
    while True:
        run_once(dry_run=dry_run)
        now = int(time.time())
        try:
            delay = max(1, _next_scheduled_at(now) - now)
        except Exception as exc:
            delay = 300
            print(
                f"homelab-arr-missing-search: ERROR: schedule calculation failed: {exc}",
                file=sys.stderr,
            )
        time.sleep(delay)


def main(argv: list[str] | None = None) -> int:
    bootstrap_container_secrets()
    parser = argparse.ArgumentParser(description="Arr missing-search scheduler")
    parser.add_argument("--once", action="store_true", help="Run one missing-search cycle and exit")
    parser.add_argument(
        "--dry-run", action="store_true", help="Plan searches without posting commands"
    )
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="Exit non-zero when the scheduler heartbeat is stale",
    )
    args = parser.parse_args(argv)

    if args.healthcheck:
        return healthcheck()
    if args.once:
        return run_once(dry_run=args.dry_run)
    return run_loop(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
