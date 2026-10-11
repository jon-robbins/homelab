from __future__ import annotations

import httpx
import pytest

from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.arr_acquire import (
    _series_selected_files,
    arr_ensure,
    arr_lookup,
    arr_queue_remove,
    arr_status,
    canonical_ensure_action,
)


def test_arr_lookup_movie_marks_in_library(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/movie/lookup?term=Merrily&apikey=k",
        json=[{
            "title": "Merrily We Roll Along",
            "year": 2025,
            "tmdbId": 1306400,
            "imdbId": "tt32641442",
        }],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/movie?apikey=k",
        json=[{
            "id": 306,
            "title": "Merrily We Roll Along",
            "tmdbId": 1306400,
            "imdbId": "tt32641442",
            "hasFile": False,
        }],
    )
    with ArrClient("http://radarr:7878/radarr", "k") as client:
        result = arr_lookup(client, media_type="movie", query="Merrily")
    assert result["ok"] is True
    assert result["candidates"][0]["in_library"] is True
    assert result["candidates"][0]["library_id"] == 306
    assert result["candidates"][0]["has_file"] is False


def test_arr_lookup_empty_not_found(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/movie/lookup?term=zzzz&apikey=k",
        json=[],
    )
    with ArrClient("http://radarr:7878/radarr", "k") as client:
        result = arr_lookup(client, media_type="movie", query="zzzz")
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


def test_arr_lookup_series_marks_in_library(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/series/lookup?term=Breaking&apikey=k",
        json=[{
            "title": "Breaking Bad",
            "year": 2008,
            "tvdbId": 81189,
            "tmdbId": 1396,
            "imdbId": "tt0903747",
        }],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/series?apikey=k",
        json=[{
            "id": 12,
            "title": "Breaking Bad",
            "tvdbId": 81189,
            "tmdbId": 1396,
            "imdbId": "tt0903747",
            "statistics": {"episodeFileCount": 0},
        }],
    )
    with ArrClient("http://sonarr:8989/sonarr", "k") as client:
        result = arr_lookup(client, media_type="series", query="Breaking")
    assert result["ok"] is True
    assert result["candidates"][0]["in_library"] is True
    assert result["candidates"][0]["library_id"] == 12
    assert result["candidates"][0]["has_file"] is False


def test_arr_ensure_movie_adds_without_search(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/movie?apikey=k",
        json=[],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/movie/lookup?term=tmdb:1306400&apikey=k",
        json=[{"title": "Merrily We Roll Along", "tmdbId": 1306400, "year": 2025}],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/rootfolder?apikey=k",
        json=[{"id": 1, "path": "/movies"}],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/qualityprofile?apikey=k",
        json=[{"id": 1, "name": "Any"}, {"id": 4, "name": "HD-1080p"}],
    )
    httpx_mock.add_response(
        method="POST",
        url="http://radarr:7878/radarr/api/v3/movie?apikey=k",
        json={
            "id": 306,
            "title": "Merrily We Roll Along",
            "path": "/movies/Merrily We Roll Along (2025)",
            "monitored": True,
            "tmdbId": 1306400,
        },
        status_code=201,
    )
    with ArrClient("http://radarr:7878/radarr", "k") as client:
        result = arr_ensure(client, media_type="movie", tmdb_id=1306400)
    assert result["ok"] is True
    assert result["created"] is True
    assert result["library_id"] == 306
    assert all("/command" not in str(r.url) for r in httpx_mock.get_requests())


def test_arr_ensure_series_monitors_season_without_search(httpx_mock) -> None:
    series = {
        "id": 10,
        "title": "Example",
        "tvdbId": 100,
        "monitored": True,
        "path": "/tv/Example",
        "seasons": [{"seasonNumber": 2, "monitored": False}],
        "qualityProfileId": 4,
        "rootFolderPath": "/tv",
    }
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/series?apikey=k",
        json=[series],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/episode?seriesId=10&seasonNumber=2&apikey=k",
        json=[{"id": 501, "episodeNumber": 1, "monitored": False}],
    )
    httpx_mock.add_response(
        method="PUT",
        url="http://sonarr:8989/sonarr/api/v3/episode/monitor?apikey=k",
        json={},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/series/10?apikey=k",
        json=series,
    )
    httpx_mock.add_response(
        method="PUT",
        url="http://sonarr:8989/sonarr/api/v3/series/10?apikey=k",
        json={**series, "seasons": [{"seasonNumber": 2, "monitored": True}]},
    )
    with ArrClient("http://sonarr:8989/sonarr", "k") as client:
        result = arr_ensure(client, media_type="series", tvdb_id=100, season=2)
    assert result["ok"] is True
    assert result["created"] is False
    assert result["library_id"] == 10
    assert all("/command" not in str(r.url) for r in httpx_mock.get_requests())


def test_canonical_ensure_action() -> None:
    assert canonical_ensure_action(media_type="movie", tmdb_id=1306400) == "ensure:movie:1306400"
    assert canonical_ensure_action(media_type="series", tvdb_id=100, season=2) == "ensure:series:100:s2"


def test_arr_status_movie_includes_queue_and_history(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/movie/306?apikey=k",
        json={"id": 306, "title": "Merrily", "hasFile": False, "movieFile": None},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/queue?page=1&pageSize=50&includeUnknownMovieItems=true&apikey=k",
        json={"records": [{"id": 1, "movieId": 306, "title": "OFT", "status": "downloading", "sizeleft": 100, "downloadId": "abc"}]},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/history?movieIds=306&pageSize=10&apikey=k",
        json={"records": [{"eventType": "grabbed", "sourceTitle": "OFT", "date": "2026-08-08T05:59:16Z"}]},
    )
    with ArrClient("http://radarr:7878/radarr", "k") as client:
        result = arr_status(client, media_type="movie", library_id=306)
    assert result["ok"] is True
    assert result["has_file"] is False
    assert result["queue"][0]["id"] == 1
    assert result["history"][0]["eventType"] == "grabbed"


def test_arr_status_movie_files_include_the_selected_completed_file(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/movie/306?apikey=k",
        json={
            "id": 306,
            "title": "Merrily",
            "hasFile": True,
            "movieFile": {"id": 700},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/queue?page=1&pageSize=50&includeUnknownMovieItems=true&apikey=k",
        json={"records": []},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/history?movieIds=306&pageSize=10&apikey=k",
        json={"records": []},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/moviefile?movieId=306&apikey=k",
        json=[
            {"id": 700, "path": "/movies/Merrily/Merrily.mkv", "size": 1234},
            {"id": 701, "path": "/movies/Merrily/alternate.mkv", "size": 5678},
        ],
    )

    with ArrClient("http://radarr:7878/radarr", "k") as client:
        result = arr_status(client, media_type="movie", library_id=306)

    assert result["files"] == [
        {
            "path": "/movies/Merrily/Merrily.mkv",
            "season": None,
            "episode": None,
            "title": "Merrily",
            "size": 1234,
        }
    ]


def test_arr_status_series_files_exclude_missing_episodes_and_other_seasons(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/series/10?apikey=k",
        json={"id": 10, "title": "Example Show", "statistics": {"episodeFileCount": 2}},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/queue?page=1&pageSize=50&includeUnknownMovieItems=true&apikey=k",
        json={"records": []},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/history?seriesIds=10&pageSize=10&seasonNumber=2&apikey=k",
        json={"records": []},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/episode?seriesId=10&seasonNumber=2&apikey=k",
        json=[
            {
                "id": 201,
                "seasonNumber": 2,
                "episodeNumber": 1,
                "title": "Selected",
                "hasFile": True,
                "episodeFileId": 901,
            },
            {
                "id": 202,
                "seasonNumber": 2,
                "episodeNumber": 2,
                "title": "Missing",
                "hasFile": False,
                "episodeFileId": None,
            },
            {
                "id": 301,
                "seasonNumber": 3,
                "episodeNumber": 1,
                "title": "Other season",
                "hasFile": True,
                "episodeFileId": 902,
            },
        ],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/episodefile/901?apikey=k",
        json={"id": 901, "path": "/tv/Example Show/Season 02/Selected.mkv", "size": 4321},
    )

    with ArrClient("http://sonarr:8989/sonarr", "k") as client:
        result = arr_status(client, media_type="series", library_id=10, season=2)

    assert result["files"] == [
        {
            "path": "/tv/Example Show/Season 02/Selected.mkv",
            "season": 2,
            "episode": 1,
            "title": "Selected",
            "size": 4321,
        }
    ]


def test_arr_status_series_files_omits_not_found_episode_files(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/series/10?apikey=k",
        json={"id": 10, "title": "Example Show", "statistics": {"episodeFileCount": 2}},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/queue?page=1&pageSize=50&includeUnknownMovieItems=true&apikey=k",
        json={"records": []},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/history?seriesIds=10&pageSize=10&seasonNumber=2&apikey=k",
        json={"records": []},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/episode?seriesId=10&seasonNumber=2&apikey=k",
        json=[
            {
                "id": 201,
                "seasonNumber": 2,
                "episodeNumber": 1,
                "title": "Removed file",
                "hasFile": True,
                "episodeFileId": 901,
            },
            {
                "id": 202,
                "seasonNumber": 2,
                "episodeNumber": 2,
                "title": "Available file",
                "hasFile": True,
                "episodeFileId": 902,
            },
        ],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/episodefile/901?apikey=k",
        status_code=404,
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/episodefile/902?apikey=k",
        json={"id": 902, "path": "/tv/Example Show/Season 02/Available.mkv", "size": 4321},
    )

    with ArrClient("http://sonarr:8989/sonarr", "k") as client:
        result = arr_status(client, media_type="series", library_id=10, season=2)

    assert result["files"] == [
        {
            "path": "/tv/Example Show/Season 02/Available.mkv",
            "season": 2,
            "episode": 2,
            "title": "Available file",
            "size": 4321,
        }
    ]


def test_series_selected_files_propagates_episode_file_errors_other_than_not_found(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/episodefile/901?apikey=k",
        status_code=500,
    )

    with ArrClient("http://sonarr:8989/sonarr", "k") as client, pytest.raises(httpx.HTTPStatusError):
        _series_selected_files(
            client,
            [
                {
                    "seasonNumber": 2,
                    "episodeNumber": 1,
                    "title": "Unavailable",
                    "hasFile": True,
                    "episodeFileId": 901,
                }
            ],
            season=2,
        )


def test_arr_status_series_files_do_not_count_other_season_files(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/series/10?apikey=k",
        json={"id": 10, "title": "Example Show", "statistics": {"episodeFileCount": 1}},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/queue?page=1&pageSize=50&includeUnknownMovieItems=true&apikey=k",
        json={"records": []},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/history?seriesIds=10&pageSize=10&seasonNumber=2&apikey=k",
        json={"records": []},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/episode?seriesId=10&seasonNumber=2&apikey=k",
        json=[
            {
                "id": 301,
                "seasonNumber": 3,
                "episodeNumber": 1,
                "title": "Other season",
                "hasFile": True,
                "episodeFileId": 902,
            }
        ],
    )

    with ArrClient("http://sonarr:8989/sonarr", "k") as client:
        result = arr_status(client, media_type="series", library_id=10, season=2)

    assert result["has_file"] is False
    assert result["files"] == []


def test_arr_status_compacts_queue_and_history_fields(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/series/10?apikey=k",
        json={"id": 10, "title": "Example", "statistics": {}},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/queue?page=1&pageSize=50&includeUnknownMovieItems=true&apikey=k",
        json={"records": [{"id": 1, "seriesId": 10, "title": "Episode", "status": "downloading", "size": 10, "sizeleft": 5, "secret": "x"}]},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/history?seriesIds=10&pageSize=10&apikey=k",
        json={"records": [{"eventType": "grabbed", "sourceTitle": "Episode", "date": "2026-08-08T05:59:16Z", "secret": "x"}]},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/episode?seriesId=10&apikey=k",
        json=[],
    )
    with ArrClient("http://sonarr:8989/sonarr", "k") as client:
        result = arr_status(client, media_type="series", library_id=10)

    assert set(result["queue"][0]) == {
        "id", "title", "status", "tracked_download_status", "download_ids",
        "size_bytes", "sizeleft_bytes", "progress_pct", "active",
    }
    assert set(result["history"][0]) == {"eventType", "sourceTitle", "date"}
    assert result["has_file"] is None


def test_arr_queue_remove_calls_delete(httpx_mock) -> None:
    httpx_mock.add_response(
        method="DELETE",
        url="http://radarr:7878/radarr/api/v3/queue/1089260099?removeFromClient=true&blocklist=false&skipRedownload=true&apikey=k",
        json={},
        status_code=200,
    )
    with ArrClient("http://radarr:7878/radarr", "k") as client:
        result = arr_queue_remove(client, queue_id=1089260099, remove_from_client=True, blocklist=False)
    assert result["ok"] is True
    assert result["removed"] is True
