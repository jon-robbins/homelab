from __future__ import annotations

from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.sonarr_releases import ensure_season_monitored_and_search


def test_ensure_season_requires_series_target() -> None:
    with ArrClient("http://sonarr:8989/sonarr", "sonarr-key") as client:
        result = ensure_season_monitored_and_search(client, season_number=11)
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_ensure_season_monitors_and_searches(httpx_mock) -> None:
    series_body = {
        "id": 226,
        "title": "90 Day Fiancé",
        "seriesType": "standard",
        "seasons": [
            {"seasonNumber": 11, "monitored": False},
            {"seasonNumber": 12, "monitored": True},
        ],
    }
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/series/226?apikey=sonarr-key",
        json=series_body,
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/episode?seriesId=226&seasonNumber=11&apikey=sonarr-key",
        json=[
            {"id": 1, "episodeNumber": 1, "monitored": False},
            {"id": 2, "episodeNumber": 2, "monitored": False},
        ],
    )
    httpx_mock.add_response(
        method="PUT",
        url="http://sonarr:8989/sonarr/api/v3/episode/monitor?apikey=sonarr-key",
        json=[],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/series/226?apikey=sonarr-key",
        json=series_body,
    )
    httpx_mock.add_response(
        method="PUT",
        url="http://sonarr:8989/sonarr/api/v3/series/226?apikey=sonarr-key",
        json={"id": 226},
    )
    httpx_mock.add_response(
        method="POST",
        url="http://sonarr:8989/sonarr/api/v3/command?apikey=sonarr-key",
        json={"id": 99, "name": "SeasonSearch", "status": "queued"},
    )
    with ArrClient("http://sonarr:8989/sonarr", "sonarr-key") as client:
        result = ensure_season_monitored_and_search(
            client, series_id=226, season_number=11
        )
    assert result["ok"] is True
    assert result["series"]["id"] == 226
    assert result["season"] == 11
    assert result["episodes_monitored"] == 2
    assert result["command"]["id"] == 99
    assert result["expected_action"] == "sonarr-season-ensure:226:11"


def test_ensure_season_no_episodes(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/series/10?apikey=sonarr-key",
        json={"id": 10, "title": "Empty", "seriesType": "standard", "seasons": []},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/episode?seriesId=10&seasonNumber=1&apikey=sonarr-key",
        json=[],
    )
    with ArrClient("http://sonarr:8989/sonarr", "sonarr-key") as client:
        result = ensure_season_monitored_and_search(client, series_id=10, season_number=1)
    assert result["ok"] is False
    assert result["error"]["code"] == "NO_EPISODES_FOR_SEASON"
