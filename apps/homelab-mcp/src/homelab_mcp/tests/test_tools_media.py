from __future__ import annotations

from dataclasses import replace
import json
from unittest.mock import patch

from homelab_mcp.tools import media

SONARR_QUEUE_URL = (
    "http://sonarr:8989/sonarr/api/v3/queue"
    "?page=1&pageSize=50&includeUnknownMovieItems=true&apikey=sonarr-key"
)
RADARR_QUEUE_URL = (
    "http://radarr:7878/radarr/api/v3/queue"
    "?page=1&pageSize=50&includeUnknownMovieItems=true&apikey=radarr-key"
)


@patch("homelab_mcp.tools.media.ArrClient")
def test_sonarr_queue_snapshot(mock_cls, mcp_settings) -> None:
    mock_client = mock_cls.return_value.__enter__.return_value
    mock_client.queue.return_value = {
        "totalRecords": 1,
        "records": [
            {"id": 1, "title": "Show S01E01", "status": "downloading", "size": 100, "sizeleft": 50}
        ],
    }
    body = json.loads(media.sonarr_queue_snapshot(mcp_settings))
    assert body["ok"] is True
    assert body["tool"] == "sonarr_queue_snapshot"
    assert body["data"]["active_count"] == 1


@patch("homelab_mcp.tools.media.ArrClient")
def test_radarr_queue_snapshot(mock_cls, mcp_settings) -> None:
    mock_client = mock_cls.return_value.__enter__.return_value
    mock_client.queue.return_value = {
        "totalRecords": 1,
        "records": [
            {"id": 9, "title": "Movie", "status": "queued", "size": 200, "sizeleft": 200}
        ],
    }
    body = json.loads(media.radarr_queue_snapshot(mcp_settings))
    assert body["ok"] is True
    assert body["tool"] == "radarr_queue_snapshot"
    assert body["data"]["total"] == 1


@patch("homelab_mcp.tools.media.ProwlarrClient")
def test_prowlarr_indexer_health(mock_cls, mcp_settings) -> None:
    mock_client = mock_cls.return_value.__enter__.return_value
    mock_client.indexers.return_value = [
        {
            "id": 1,
            "name": "ExampleTracker",
            "enable": True,
            "priority": 25,
            "protocol": "torrent",
            "implementation": "Cardigann",
            "status": "healthy",
        },
        {
            "id": 2,
            "name": "Disabled",
            "enable": False,
            "priority": 50,
            "protocol": "torrent",
            "implementation": "Cardigann",
        },
    ]
    body = json.loads(media.prowlarr_indexer_health(mcp_settings))
    assert body["ok"] is True
    assert body["tool"] == "prowlarr_indexer_health"
    assert body["data"]["total"] == 2
    assert body["data"]["enabled_count"] == 1
    assert body["data"]["disabled_count"] == 1
    assert body["data"]["indexers"][0]["indexer_test_not_run"] is True


@patch("homelab_mcp.tools.media.ProwlarrClient")
def test_prowlarr_search_drops_raw_release_rows(mock_cls, mcp_settings) -> None:
    mock_client = mock_cls.return_value.__enter__.return_value
    mock_client.search.return_value = [
        {"title": "Release", "guid": "g1", "indexerId": 1, "size": 1000, "seeders": 2}
    ]

    body = json.loads(media.prowlarr_indexer_search(mcp_settings, query="Release"))

    assert body["data"]["options"][0]["title"] == "Release"
    assert "release" not in body["data"]["options"][0]


def test_overseerr_request_summary(httpx_mock, mcp_settings) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://overseerr:5055/api/v1/request/count",
        match_headers={"X-Api-Key": "overseerr-key"},
        json={"pending": 2, "approved": 1, "processing": 3, "available": 10, "declined": 4},
    )
    body = json.loads(media.overseerr_request_summary(mcp_settings))
    assert body["ok"] is True
    assert body["tool"] == "overseerr_request_summary"
    assert body["data"]["open_count"] == 6


@patch("homelab_mcp.tools.media.QBittorrentClient")
def test_overseerr_request_pipeline_matches_sonarr_queue_hash(
    mock_qbt_cls, httpx_mock, mcp_settings
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://overseerr:5055/api/v1/request?take=20&skip=0",
        match_headers={"X-Api-Key": "overseerr-key"},
        json={
            "results": [
                {
                    "id": 7,
                    "type": "tv",
                    "status": 2,
                    "requestedBy": {"displayName": "Jon"},
                    "media": {
                        "mediaType": "tv",
                        "title": "Example Show",
                        "tvdbId": 1234,
                        "externalServiceId": 55,
                    },
                }
            ]
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=SONARR_QUEUE_URL,
        json={
            "records": [
                {
                    "id": 100,
                    "seriesId": 55,
                    "title": "Example Show S01E01",
                    "status": "downloading",
                    "downloadId": "a" * 40,
                    "trackedDownloadStatus": "ok",
                    "size": 100,
                    "sizeleft": 50,
                }
            ],
            "totalRecords": 1,
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=RADARR_QUEUE_URL,
        json={"records": [], "totalRecords": 0},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/history?seriesIds=55&pageSize=20&apikey=sonarr-key",
        json={"records": []},
    )

    mock_qbt = mock_qbt_cls.return_value.__enter__.return_value
    mock_qbt.torrents_info_raw.return_value = [
        {
            "hash": "a" * 40,
            "name": "Example Show S01E01",
            "category": "tv-sonarr",
            "state": "downloading",
            "progress": 0.5,
        }
    ]

    body = json.loads(media.overseerr_request_pipeline(mcp_settings, limit=20))

    assert body["ok"] is True
    assert body["tool"] == "overseerr_request_pipeline"
    assert body["data"]["summary"]["qbt_present"] == 1
    item = body["data"]["requests"][0]
    assert item["stage"] == "qbt_active"
    assert item["arr"]["service"] == "sonarr"
    assert item["arr"]["download_hashes"] == ["a" * 40]
    assert item["qbt"]["hash"] == "a" * 40


@patch("homelab_mcp.tools.media.QBittorrentClient")
def test_overseerr_request_pipeline_flags_grabbed_hash_missing_from_qbt(
    mock_qbt_cls, httpx_mock, mcp_settings
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://overseerr:5055/api/v1/request?take=20&skip=0",
        match_headers={"X-Api-Key": "overseerr-key"},
        json={
            "results": [
                {
                    "id": 8,
                    "type": "movie",
                    "status": 2,
                    "media": {
                        "mediaType": "movie",
                        "title": "Example Movie",
                        "tmdbId": 456,
                        "externalServiceId": 306,
                    },
                }
            ]
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=SONARR_QUEUE_URL,
        json={"records": [], "totalRecords": 0},
    )
    httpx_mock.add_response(
        method="GET",
        url=RADARR_QUEUE_URL,
        json={"records": [], "totalRecords": 0},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/history?movieIds=306&pageSize=20&apikey=radarr-key",
        json={
            "records": [
                {
                    "eventType": "grabbed",
                    "sourceTitle": "Example Movie 2026",
                    "date": "2026-08-16T01:10:00Z",
                    "downloadId": "b" * 40,
                }
            ]
        },
    )

    mock_qbt = mock_qbt_cls.return_value.__enter__.return_value
    mock_qbt.torrents_info_raw.return_value = []

    body = json.loads(media.overseerr_request_pipeline(mcp_settings, limit=20))

    assert body["ok"] is True
    assert body["data"]["summary"]["missing_qbt"] == 1
    item = body["data"]["requests"][0]
    assert item["stage"] == "arr_grabbed_missing_qbt"
    assert item["diagnosis"] == "arr has a qBT download hash but qBT has no matching torrent"
    assert item["arr"]["download_hashes"] == ["b" * 40]


def test_overseerr_request_pipeline_sanitizes_arr_error_urls(httpx_mock, mcp_settings) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://overseerr:5055/api/v1/request?take=20&skip=0",
        match_headers={"X-Api-Key": "overseerr-key"},
        json={
            "results": [
                {
                    "id": 9,
                    "type": "tv",
                    "media": {"mediaType": "tv", "title": "Broken Show", "externalServiceId": 55},
                }
            ]
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=SONARR_QUEUE_URL,
        status_code=500,
        json={"error": "boom"},
    )

    raw = media.overseerr_request_pipeline(mcp_settings, limit=20)
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["error"]["code"] == "UPSTREAM_ERROR"
    assert "apikey=" not in raw
    assert "sonarr-key" not in raw


def test_bazarr_missing_subtitles_summary(httpx_mock, mcp_settings) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://bazarr:6767/bazarr/api/movies?start=0&length=100",
        json={
            "total": 1,
            "data": [{"radarrId": 10, "title": "Movie A", "missing_subtitles": [{"code2": "en"}]}],
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="http://bazarr:6767/bazarr/api/episodes?start=0&length=100",
        json={
            "total": 1,
            "data": [{"sonarrEpisodeId": 20, "seriesTitle": "Show B", "missing_subtitles": ["es"]}],
        },
    )
    body = json.loads(media.bazarr_missing_subtitles_summary(mcp_settings, scan_limit=100))
    assert body["ok"] is True
    assert body["tool"] == "bazarr_missing_subtitles_summary"
    assert body["data"]["movies_with_missing"] == 1
    assert body["data"]["episodes_with_missing"] == 1
    assert body["data"]["missing_by_language"] == {"en": 1, "es": 1}


def test_bazarr_missing_subtitles_summary_requires_api_key(mcp_settings) -> None:
    settings = replace(mcp_settings, bazarr_api_key="")
    body = json.loads(media.bazarr_missing_subtitles_summary(settings))
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"


def test_media_paths_usage_reports_disk(tmp_path, mcp_settings) -> None:
    nvme = tmp_path / "nvme"
    hdd = tmp_path / "hdd"
    nvme.mkdir()
    hdd.mkdir()
    settings = replace(mcp_settings, media_nvme_mount=str(nvme), media_hdd_mount=str(hdd))
    body = json.loads(media.media_paths_usage(settings))
    assert body["ok"] is True
    assert body["tool"] == "media_paths_usage"
    assert body["data"]["paths"][0]["status"] == "ok"
    assert body["data"]["paths"][0]["total_bytes"] > 0


@patch("homelab_mcp.tools.media.worker_arr_indexer_sync_health")
@patch("homelab_mcp.tools.media.ArrClient")
def test_arr_indexer_sync_health(mock_cls, mock_health, mcp_settings) -> None:
    mock_health.return_value = {
        "radarr": {"enabled_indexers": 2, "healthy": True},
        "sonarr": {"enabled_indexers": 1, "healthy": True},
        "healthy": True,
        "hint": None,
    }
    body = json.loads(media.arr_indexer_sync_health(mcp_settings))
    assert body["ok"] is True
    assert body["tool"] == "arr_indexer_sync_health"
    assert body["data"]["healthy"] is True


@patch("homelab_mcp.tools.media.search_radarr_releases")
@patch("homelab_mcp.tools.media.ArrClient")
def test_radarr_release_search(mock_cls, mock_search, mcp_settings) -> None:
    mock_search.return_value = {
        "ok": True,
        "type": "movie",
        "movie": {"id": 42, "title": "Her Story"},
        "options": [{"rank": 1, "guid": "g1", "movie_id": 42, "size_bytes": 1000}],
    }
    body = json.loads(media.radarr_release_search(mcp_settings, query="Her Story"))
    assert body["ok"] is True
    assert body["tool"] == "radarr_release_search"
    assert body["data"]["options"][0]["movie_id"] == 42


def test_radarr_release_search_requires_api_key(mcp_settings) -> None:
    settings = replace(mcp_settings, radarr_api_key="")
    body = json.loads(media.radarr_release_search(settings, query="Her Story"))
    assert body["ok"] is False
    assert body["error"]["code"] == "RADARR_NOT_CONFIGURED"
