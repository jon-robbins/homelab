from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from homelab_workers.shared.arr_missing_search import (
    MissingItem,
    allocate_quotas,
    select_missing_batch,
    parse_sonarr_missing_records,
    parse_radarr_missing_records,
    seconds_until_next_run,
)
from homelab_workers.shared.arr_missing_search_state import (
    build_missing_search_status,
    evaluate_missing_search_health,
    read_missing_search_status,
    write_missing_search_status,
)


def test_allocate_quotas_prefers_split_and_rebalances():
    assert allocate_quotas(
        cap=50, sonarr_quota=25, radarr_quota=25, sonarr_available=10, radarr_available=100
    ) == (10, 40)
    assert allocate_quotas(
        cap=50, sonarr_quota=25, radarr_quota=25, sonarr_available=100, radarr_available=5
    ) == (45, 5)
    assert allocate_quotas(
        cap=50, sonarr_quota=25, radarr_quota=25, sonarr_available=3, radarr_available=2
    ) == (3, 2)


def test_select_missing_batch_oldest_first_and_cap():
    sonarr = [
        MissingItem("sonarr", 2, "2026-06-01", "B", 10),
        MissingItem("sonarr", 1, "2026-05-01", "A", 10),
    ]
    radarr = [
        MissingItem("radarr", 9, "2026-04-01", "M", 20),
    ]
    selected = select_missing_batch(
        sonarr, radarr, cap=2, sonarr_quota=1, radarr_quota=1
    )
    assert [i.item_id for i in selected] == [1, 9]


def test_parse_skips_queue_ids():
    records = [
        {
            "id": 1,
            "seasonNumber": 12,
            "episodeNumber": 1,
            "title": "Ep1",
            "airDate": "2026-05-10",
            "seriesId": 226,
            "series": {"title": "Show"},
        }
    ]
    assert parse_sonarr_missing_records(records, queue_episode_ids={1}) == []
    parsed = parse_sonarr_missing_records(records, queue_episode_ids=set())
    assert parsed[0].item_id == 1
    assert parsed[0].sort_date == "2026-05-10"

    movies = [
        {
            "id": 302,
            "title": "Film",
            "inCinemas": "2026-03-01",
            "digitalRelease": None,
            "physicalRelease": None,
        }
    ]
    assert parse_radarr_missing_records(movies, queue_movie_ids={302}) == []
    assert parse_radarr_missing_records(movies, queue_movie_ids=set())[0].item_id == 302


def test_seconds_until_next_run_same_day_and_next_day():
    tz = ZoneInfo("UTC")
    now = datetime(2026, 7, 26, 3, 0, tzinfo=tz)
    assert seconds_until_next_run(now=now, hour=4, minute=15) == 75 * 60
    now2 = datetime(2026, 7, 26, 5, 0, tzinfo=tz)
    assert seconds_until_next_run(now=now2, hour=4, minute=15) == (23 * 60 + 15) * 60


def test_status_roundtrip(tmp_path: Path):
    path = tmp_path / "status.json"
    payload = build_missing_search_status(
        enabled=True,
        running=False,
        ok=True,
        dry_run=False,
        started_at=100,
        completed_at=110,
        next_scheduled_at=200,
        sonarr_missing_total=12,
        radarr_missing_total=3,
        sonarr_searched=5,
        radarr_searched=2,
        consecutive_failures=0,
        last_error=None,
    )
    write_missing_search_status(path, payload)
    loaded = read_missing_search_status(path)
    assert loaded is not None
    assert loaded["sonarr_searched"] == 5
    assert loaded["ok"] is True


def test_health_disabled_is_healthy():
    h = evaluate_missing_search_health(
        {"enabled": False, "ok": True, "updated_at": 1},
        max_age_seconds=10,
        now=100,
    )
    assert h["healthy"] is True


def test_health_stale_when_enabled():
    h = evaluate_missing_search_health(
        {"enabled": True, "ok": True, "updated_at": 1},
        max_age_seconds=10,
        now=100,
    )
    assert h["healthy"] is False
