from __future__ import annotations

import os
import sys

from homelab_workers.shared.qbittorrent import QBittorrentClient
from homelab_workers.shared.seed_slot_optimizer import apply_seed_slots, plan_seed_slots


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_tags(name: str, default: str) -> frozenset[str]:
    raw = os.environ.get(name, default)
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def main() -> int:
    qbt_url = (
        os.environ.get("QBITTORRENT_INTERNAL_URL")
        or os.environ.get("QBITTORRENT_URL")
        or "http://qbittorrent:8080"
    )
    qbt_user = os.environ.get("QBITTORRENT_USERNAME", "")
    qbt_pass = os.environ.get("QBITTORRENT_PASSWORD", "")
    slot_limit = _env_int("QBT_SEED_SLOT_LIMIT", 200)
    recency_hours = _env_int("QBT_SEED_SLOT_RECENCY_HOURS", 48)
    tracker_tags = _env_tags("QBT_SEED_SLOT_TAGS", "fictional-seed,fictional-tracker,freeleech")

    try:
        with QBittorrentClient(qbt_url, qbt_user, qbt_pass) as client:
            torrents = client.torrents_info_raw()
            plan = plan_seed_slots(
                torrents,
                slot_limit=slot_limit,
                recency_hours=recency_hours,
                tracker_tags=tracker_tags,
            )
            applied = apply_seed_slots(client, plan)
    except Exception as exc:
        print(f"qbt-seed-slots: ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        "qbt-seed-slots: "
        f"candidates={applied['candidate_count']} "
        f"priority={applied['priority_count']} "
        f"resumed={applied['applied_resume_count']} "
        f"paused={applied['applied_pause_count']}"
    )
    for warning in applied.get("warnings") or []:
        print(f"qbt-seed-slots: WARNING: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
