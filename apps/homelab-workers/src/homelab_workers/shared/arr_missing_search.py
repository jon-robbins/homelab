from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal


@dataclass(frozen=True)
class MissingItem:
    app: Literal["sonarr", "radarr"]
    item_id: int
    sort_date: str
    title: str
    series_or_movie_id: int


def allocate_quotas(
    *,
    cap: int,
    sonarr_quota: int,
    radarr_quota: int,
    sonarr_available: int,
    radarr_available: int,
) -> tuple[int, int]:
    if cap < 0:
        raise ValueError("cap must be >= 0")
    s_take = min(max(sonarr_quota, 0), sonarr_available, cap)
    r_take = min(max(radarr_quota, 0), radarr_available, cap - s_take)
    # Rebalance leftover toward the side that still has availability.
    remaining = cap - s_take - r_take
    if remaining > 0:
        extra_s = min(remaining, max(0, sonarr_available - s_take))
        s_take += extra_s
        remaining -= extra_s
    if remaining > 0:
        extra_r = min(remaining, max(0, radarr_available - r_take))
        r_take += extra_r
    return s_take, r_take


def select_missing_batch(
    sonarr_items: list[MissingItem],
    radarr_items: list[MissingItem],
    *,
    cap: int,
    sonarr_quota: int,
    radarr_quota: int,
) -> list[MissingItem]:
    sonarr_sorted = sorted(sonarr_items, key=lambda i: (i.sort_date, i.item_id))
    radarr_sorted = sorted(radarr_items, key=lambda i: (i.sort_date, i.item_id))
    s_n, r_n = allocate_quotas(
        cap=cap,
        sonarr_quota=sonarr_quota,
        radarr_quota=radarr_quota,
        sonarr_available=len(sonarr_sorted),
        radarr_available=len(radarr_sorted),
    )
    return sonarr_sorted[:s_n] + radarr_sorted[:r_n]


def parse_sonarr_missing_records(
    records: list[dict[str, Any]], queue_episode_ids: set[int]
) -> list[MissingItem]:
    out: list[MissingItem] = []
    for row in records:
        eid = int(row["id"])
        if eid in queue_episode_ids:
            continue
        series = row.get("series") if isinstance(row.get("series"), dict) else {}
        show = str(series.get("title") or "")
        ep_title = str(row.get("title") or "")
        title = f"{show} S{int(row.get('seasonNumber') or 0):02d}E{int(row.get('episodeNumber') or 0):02d} {ep_title}".strip()
        sort_date = str(row.get("airDate") or row.get("airDateUtc") or "9999-12-31")[:10]
        out.append(
            MissingItem(
                app="sonarr",
                item_id=eid,
                sort_date=sort_date,
                title=title,
                series_or_movie_id=int(row.get("seriesId") or series.get("id") or 0),
            )
        )
    return out


def _radarr_sort_date(row: dict[str, Any]) -> str:
    for key in ("digitalRelease", "physicalRelease", "inCinemas"):
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:10]
    return "9999-12-31"


def parse_radarr_missing_records(
    records: list[dict[str, Any]], queue_movie_ids: set[int]
) -> list[MissingItem]:
    out: list[MissingItem] = []
    for row in records:
        mid = int(row["id"])
        if mid in queue_movie_ids:
            continue
        out.append(
            MissingItem(
                app="radarr",
                item_id=mid,
                sort_date=_radarr_sort_date(row),
                title=str(row.get("title") or mid),
                series_or_movie_id=mid,
            )
        )
    return out


def seconds_until_next_run(*, now: datetime, hour: int, minute: int) -> int:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1, int((target - now).total_seconds()))
