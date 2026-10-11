from __future__ import annotations

from homelab_workers.shared.prowlarr_releases import grab_prowlarr_release, search_prowlarr_releases
from homelab_workers.shared.prowlarr import ProwlarrClient


def test_search_prowlarr_sort_and_indexer_ids(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://prowlarr:9696/prowlarr/api/v1/search?query=PlayStation&limit=10&type=search&indexerIds=7",
        json=[
            {"title": "small", "seeders": 1, "size": 100, "indexerId": 7, "guid": "g1"},
            {"title": "big", "seeders": 10, "size": 200, "indexerId": 7, "guid": "g2"},
        ],
    )
    with ProwlarrClient("http://prowlarr:9696/prowlarr", "key") as client:
        result = search_prowlarr_releases(client, query="PlayStation", indexer_ids=[7])
    assert result["ok"] is True
    assert result["options"][0]["title"] == "big"


def test_search_includes_stable_id_parameters(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url=(
            "http://prowlarr:9696/prowlarr/api/v1/search?query=Stag&type=search&limit=10"
            "&tvdbId=200&tmdbId=100&imdbId=tt1234567"
        ),
        json=[],
    )
    with ProwlarrClient("http://prowlarr:9696/prowlarr", "key") as client:
        assert client.search(
            query="Stag", tvdb_id=200, tmdb_id=100, imdb_id="tt1234567"
        ) == []


def test_grab_prowlarr_not_cached(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://prowlarr:9696/prowlarr/api/v1/search",
        status_code=404,
    )
    with ProwlarrClient("http://prowlarr:9696/prowlarr", "key") as client:
        result = grab_prowlarr_release(client, guid="g1", indexer_id=7)
    assert result["ok"] is False
    assert result["error"]["code"] == "RELEASE_NOT_CACHED"
