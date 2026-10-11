from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARR_MISSING_SEARCH_STATUS_PATH", str(tmp_path / "status.json"))
    monkeypatch.setenv("ARR_MISSING_SEARCH_LOCK_PATH", str(tmp_path / "search.lock"))
    monkeypatch.setenv("SONARR_INTERNAL_URL", "http://sonarr.test/sonarr")
    monkeypatch.setenv("RADARR_INTERNAL_URL", "http://radarr.test/radarr")
    monkeypatch.setenv("SONARR_API_KEY", "s-key")
    monkeypatch.setenv("RADARR_API_KEY", "r-key")
    monkeypatch.setenv("TZ", "UTC")


def test_disabled_once_writes_healthy_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ARR_MISSING_SEARCH_ENABLED", "0")
    from homelab_workers.cli.arr_missing_search import main

    assert main(["--once"]) == 0

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["enabled"] is False
    assert status["ok"] is True
    assert capsys.readouterr().out.strip() == "homelab-arr-missing-search: disabled"


def test_disabled_once_ignores_invalid_prior_status_and_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARR_MISSING_SEARCH_ENABLED", "0")
    monkeypatch.setenv("TZ", "Not/A_Timezone")
    monkeypatch.setenv("ARR_MISSING_SEARCH_HOUR", "not-an-hour")
    (tmp_path / "status.json").write_text("{not-json", encoding="utf-8")
    from homelab_workers.cli.arr_missing_search import main

    assert main(["--once"]) == 0

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["enabled"] is False
    assert status["ok"] is True


def test_dry_run_plans_without_command_post(httpx_mock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARR_MISSING_SEARCH_ENABLED", "1")
    httpx_mock.add_response(
        url="http://sonarr.test/sonarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=s-key",
        json={
            "records": [
                {
                    "id": 11,
                    "seasonNumber": 1,
                    "episodeNumber": 1,
                    "title": "E1",
                    "airDate": "2026-01-01",
                    "seriesId": 1,
                    "series": {"title": "S"},
                }
            ],
            "totalRecords": 1,
        },
    )
    httpx_mock.add_response(
        url=(
            "http://sonarr.test/sonarr/api/v3/queue"
            "?page=1&pageSize=100&includeUnknownMovieItems=true&apikey=s-key"
        ),
        json={"records": [], "totalRecords": 0},
    )
    httpx_mock.add_response(
        url="http://radarr.test/radarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=r-key",
        json={"records": [], "totalRecords": 0},
    )
    httpx_mock.add_response(
        url=(
            "http://radarr.test/radarr/api/v3/queue"
            "?page=1&pageSize=100&includeUnknownMovieItems=true&apikey=r-key"
        ),
        json={"records": [], "totalRecords": 0},
    )
    from homelab_workers.cli.arr_missing_search import main

    assert main(["--once", "--dry-run"]) == 0
    assert all("/command" not in str(request.url) for request in httpx_mock.get_requests())


def test_failure_status_redacts_api_key(
    tmp_path: Path, httpx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARR_MISSING_SEARCH_ENABLED", "1")
    httpx_mock.add_response(
        url="http://sonarr.test/sonarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=s-key",
        status_code=500,
    )
    httpx_mock.add_response(
        url="http://radarr.test/radarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=r-key",
        status_code=500,
    )
    from homelab_workers.cli.arr_missing_search import main

    assert main(["--once"]) == 1

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert "HTTPStatusError" in status["last_error"]
    assert "s-key" not in status["last_error"]
    assert "r-key" not in status["last_error"]
    assert "apikey=[REDACTED]" in status["last_error"]


def test_sonarr_search_continues_when_radarr_fetch_fails(
    tmp_path: Path, httpx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARR_MISSING_SEARCH_ENABLED", "1")
    httpx_mock.add_response(
        url="http://sonarr.test/sonarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=s-key",
        json={
            "records": [
                {
                    "id": 11,
                    "seasonNumber": 1,
                    "episodeNumber": 1,
                    "title": "E1",
                    "airDate": "2026-01-01",
                    "seriesId": 1,
                    "series": {"title": "S"},
                }
            ],
            "totalRecords": 1,
        },
    )
    httpx_mock.add_response(
        url=(
            "http://sonarr.test/sonarr/api/v3/queue"
            "?page=1&pageSize=100&includeUnknownMovieItems=true&apikey=s-key"
        ),
        json={"records": [], "totalRecords": 0},
    )
    httpx_mock.add_response(
        url="http://radarr.test/radarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=r-key",
        status_code=500,
    )
    httpx_mock.add_response(
        method="POST",
        url="http://sonarr.test/sonarr/api/v3/command?apikey=s-key",
        json={"id": 101},
    )
    from homelab_workers.cli.arr_missing_search import main

    assert main(["--once"]) == 0

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["ok"] is True
    assert status["sonarr_searched"] == 1
    assert status["radarr_searched"] == 0
    assert "radarr HTTPStatusError" in status["last_error"]
    posts = [request for request in httpx_mock.get_requests() if request.method == "POST"]
    assert [json.loads(request.read()) for request in posts] == [
        {"name": "EpisodeSearch", "episodeIds": [11]}
    ]


def test_radarr_search_continues_when_sonarr_fetch_fails(
    tmp_path: Path, httpx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARR_MISSING_SEARCH_ENABLED", "1")
    monkeypatch.setenv("ARR_MISSING_SEARCH_SONARR_QUOTA", "0")
    httpx_mock.add_response(
        url="http://sonarr.test/sonarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=s-key",
        status_code=500,
    )
    httpx_mock.add_response(
        url="http://radarr.test/radarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=r-key",
        json={
            "records": [{"id": 21, "title": "M21", "digitalRelease": "2026-01-01"}],
            "totalRecords": 1,
        },
    )
    httpx_mock.add_response(
        url=(
            "http://radarr.test/radarr/api/v3/queue"
            "?page=1&pageSize=100&includeUnknownMovieItems=true&apikey=r-key"
        ),
        json={"records": [], "totalRecords": 0},
    )
    httpx_mock.add_response(
        method="POST",
        url="http://radarr.test/radarr/api/v3/command?apikey=r-key",
        json={"id": 201},
    )
    from homelab_workers.cli.arr_missing_search import main

    assert main(["--once"]) == 0

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["ok"] is True
    assert status["sonarr_searched"] == 0
    assert status["radarr_searched"] == 1
    assert "sonarr HTTPStatusError" in status["last_error"]
    posts = [request for request in httpx_mock.get_requests() if request.method == "POST"]
    assert [json.loads(request.read()) for request in posts] == [
        {"name": "MoviesSearch", "movieIds": [21]}
    ]


def test_healthcheck_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ARR_MISSING_SEARCH_ENABLED", "0")
    from homelab_workers.cli.arr_missing_search import main

    assert main(["--once"]) == 0
    assert main(["--healthcheck"]) == 0
    assert "healthy (disabled)" in capsys.readouterr().out


def test_healthcheck_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from homelab_workers.cli.arr_missing_search import main

    assert main(["--healthcheck"]) == 1
    assert "unhealthy (missing_status)" in capsys.readouterr().err


def test_search_posts_selected_ids_in_chunks_of_30(
    httpx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARR_MISSING_SEARCH_ENABLED", "1")
    monkeypatch.setenv("ARR_MISSING_SEARCH_CAP", "31")
    monkeypatch.setenv("ARR_MISSING_SEARCH_SONARR_QUOTA", "31")
    monkeypatch.setenv("ARR_MISSING_SEARCH_RADARR_QUOTA", "0")
    episodes = [
        {
            "id": item_id,
            "seasonNumber": 1,
            "episodeNumber": item_id,
            "title": f"E{item_id}",
            "airDate": f"2026-01-{item_id:02d}",
            "seriesId": 1,
            "series": {"title": "S"},
        }
        for item_id in range(1, 32)
    ]
    httpx_mock.add_response(
        url="http://sonarr.test/sonarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=s-key",
        json={"records": episodes, "totalRecords": 31},
    )
    httpx_mock.add_response(
        url=(
            "http://sonarr.test/sonarr/api/v3/queue"
            "?page=1&pageSize=100&includeUnknownMovieItems=true&apikey=s-key"
        ),
        json={"records": [], "totalRecords": 0},
    )
    httpx_mock.add_response(
        url="http://radarr.test/radarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=r-key",
        json={"records": [], "totalRecords": 0},
    )
    httpx_mock.add_response(
        url=(
            "http://radarr.test/radarr/api/v3/queue"
            "?page=1&pageSize=100&includeUnknownMovieItems=true&apikey=r-key"
        ),
        json={"records": [], "totalRecords": 0},
    )
    httpx_mock.add_response(
        method="POST",
        url="http://sonarr.test/sonarr/api/v3/command?apikey=s-key",
        json={"id": 101},
    )
    httpx_mock.add_response(
        method="POST",
        url="http://sonarr.test/sonarr/api/v3/command?apikey=s-key",
        json={"id": 102},
    )
    from homelab_workers.cli.arr_missing_search import main

    assert main(["--once"]) == 0

    posts = [request for request in httpx_mock.get_requests() if request.method == "POST"]
    assert [json.loads(request.read()) for request in posts] == [
        {"name": "EpisodeSearch", "episodeIds": list(range(1, 31))},
        {"name": "EpisodeSearch", "episodeIds": [31]},
    ]


def test_queue_records_exclude_direct_and_nested_ids(
    httpx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARR_MISSING_SEARCH_ENABLED", "1")
    sonarr_records = [
        {
            "id": item_id,
            "seasonNumber": 1,
            "episodeNumber": item_id,
            "title": f"E{item_id}",
            "airDate": "2026-01-01",
            "seriesId": 1,
            "series": {"title": "S"},
        }
        for item_id in (11, 12)
    ]
    radarr_records = [
        {"id": item_id, "title": f"M{item_id}", "digitalRelease": "2026-01-01"}
        for item_id in (21, 22)
    ]
    httpx_mock.add_response(
        url="http://sonarr.test/sonarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=s-key",
        json={"records": sonarr_records, "totalRecords": 2},
    )
    httpx_mock.add_response(
        url=(
            "http://sonarr.test/sonarr/api/v3/queue"
            "?page=1&pageSize=100&includeUnknownMovieItems=true&apikey=s-key"
        ),
        json={"records": [{"episodeId": 11}, {"episode": {"id": 12}}], "totalRecords": 2},
    )
    httpx_mock.add_response(
        url="http://radarr.test/radarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=r-key",
        json={"records": radarr_records, "totalRecords": 2},
    )
    httpx_mock.add_response(
        url=(
            "http://radarr.test/radarr/api/v3/queue"
            "?page=1&pageSize=100&includeUnknownMovieItems=true&apikey=r-key"
        ),
        json={"records": [{"movieId": 21}, {"movie": {"id": 22}}], "totalRecords": 2},
    )
    from homelab_workers.cli.arr_missing_search import main

    assert main(["--once"]) == 0
    assert all(request.method != "POST" for request in httpx_mock.get_requests())


def test_paging_continues_without_positive_total_records() -> None:
    from homelab_workers.cli.arr_missing_search import _paged_records

    class Client:
        def __init__(self) -> None:
            self.pages: list[int] = []

        def get(self, path: str, *, query: dict[str, object]) -> dict[str, Any]:
            self.pages.append(int(query["page"]))
            if query["page"] == 1:
                return {"records": [{"id": 11}], "totalRecords": 0}
            if query["page"] == 2:
                return {"records": [{"id": 12}]}
            return {"records": []}

    client = Client()

    assert _paged_records(client, "/wanted/missing") == [{"id": 11}, {"id": 12}]
    assert client.pages == [1, 2, 3]


def test_configured_cap_is_clamped_to_50(httpx_mock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARR_MISSING_SEARCH_ENABLED", "1")
    monkeypatch.setenv("ARR_MISSING_SEARCH_CAP", "100")
    monkeypatch.setenv("ARR_MISSING_SEARCH_SONARR_QUOTA", "100")
    monkeypatch.setenv("ARR_MISSING_SEARCH_RADARR_QUOTA", "100")
    episodes = [
        {
            "id": item_id,
            "seasonNumber": 1,
            "episodeNumber": item_id,
            "title": f"E{item_id}",
            "airDate": "2026-01-01",
            "seriesId": 1,
            "series": {"title": "S"},
        }
        for item_id in range(1, 52)
    ]
    httpx_mock.add_response(
        url="http://sonarr.test/sonarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=s-key",
        json={"records": episodes, "totalRecords": 51},
    )
    httpx_mock.add_response(
        url=(
            "http://sonarr.test/sonarr/api/v3/queue"
            "?page=1&pageSize=100&includeUnknownMovieItems=true&apikey=s-key"
        ),
        json={"records": [], "totalRecords": 0},
    )
    httpx_mock.add_response(
        url="http://radarr.test/radarr/api/v3/wanted/missing?page=1&pageSize=100&apikey=r-key",
        json={"records": [], "totalRecords": 0},
    )
    httpx_mock.add_response(
        url=(
            "http://radarr.test/radarr/api/v3/queue"
            "?page=1&pageSize=100&includeUnknownMovieItems=true&apikey=r-key"
        ),
        json={"records": [], "totalRecords": 0},
    )
    for command_id in (101, 102):
        httpx_mock.add_response(
            method="POST",
            url="http://sonarr.test/sonarr/api/v3/command?apikey=s-key",
            json={"id": command_id},
        )
    from homelab_workers.cli.arr_missing_search import main

    assert main(["--once"]) == 0

    posts = [request for request in httpx_mock.get_requests() if request.method == "POST"]
    searched_ids = [
        item_id for request in posts for item_id in json.loads(request.read())["episodeIds"]
    ]
    assert searched_ids == list(range(1, 51))


def test_loop_records_setup_failure_then_sleeps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARR_MISSING_SEARCH_ENABLED", "1")
    (tmp_path / "status.json").write_text("{not-json", encoding="utf-8")
    sleep_delays: list[float] = []

    def stop_after_sleep(delay: float) -> None:
        sleep_delays.append(delay)
        raise KeyboardInterrupt

    from homelab_workers.cli import arr_missing_search

    monkeypatch.setattr(arr_missing_search.time, "sleep", stop_after_sleep)

    with pytest.raises(KeyboardInterrupt):
        arr_missing_search.run_loop(dry_run=True)

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert status["consecutive_failures"] == 1
    assert "Expecting property name" in status["last_error"]
    assert len(sleep_delays) == 1


def test_loop_retries_after_schedule_parsing_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sleep_delays: list[float] = []
    run_count = 0

    def record_run(*, dry_run: bool) -> int:
        nonlocal run_count
        run_count += 1
        return 0

    def stop_after_sleep(delay: float) -> None:
        sleep_delays.append(delay)
        raise KeyboardInterrupt

    from homelab_workers.cli import arr_missing_search

    monkeypatch.setattr(arr_missing_search, "run_once", record_run)
    monkeypatch.setattr(
        arr_missing_search,
        "_next_scheduled_at",
        lambda now: (_ for _ in ()).throw(ValueError("invalid schedule")),
    )
    monkeypatch.setattr(arr_missing_search.time, "sleep", stop_after_sleep)

    with pytest.raises(KeyboardInterrupt):
        arr_missing_search.run_loop(dry_run=True)

    assert run_count == 1
    assert sleep_delays == [300]
    assert "invalid schedule" in capsys.readouterr().err
