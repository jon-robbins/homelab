from __future__ import annotations

from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.radarr_releases import ReleaseSearchConfig, grab_radarr_release, search_radarr_releases


def test_radarr_release_search_returns_options(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/indexer?apikey=radarr-key",
        json=[{"enable": True}],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/movie?apikey=radarr-key",
        json=[{"id": 42, "title": "Test Movie", "sortTitle": "testmovie"}],
    )
    httpx_mock.add_response(
        method="POST",
        url="http://radarr:7878/radarr/api/v3/command?apikey=radarr-key",
        json={"id": 1},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/release?movieId=42&apikey=radarr-key",
        json=[{"title": "Release A", "seeders": 5, "size": 1000, "guid": "g1"}],
    )
    with ArrClient("http://radarr:7878/radarr", "radarr-key") as client:
        result = search_radarr_releases(
            client,
            query="Test Movie",
            movie_id=None,
            config=ReleaseSearchConfig(search_timeout_s=1, poll_s=0.01, options_limit=5),
        )
    assert result["ok"] is True
    assert result["options"][0]["movie_id"] == 42


def test_shape_movie_option_marks_freeleech() -> None:
    from homelab_workers.shared.radarr_releases import _shape_movie_option

    opt = _shape_movie_option(
        1,
        {
            "title": "X",
            "seeders": 1,
            "size": 10,
            "guid": "g",
            "indexerFlags": ["freeleech"],
            "rejections": ["Quality rejected", "Other"],
            "approved": False,
        },
        movie_id=42,
    )
    assert opt["indexer_flags"] == ["freeleech"]
    assert opt["is_freeleech"] is True
    assert opt["rejections"] == ["Quality rejected", "Other"]


def test_radarr_release_grab_success(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/release?movieId=42&apikey=radarr-key",
        json=[{"title": "Release A", "guid": "g1", "indexerId": 1}],
    )
    httpx_mock.add_response(
        method="POST",
        url="http://radarr:7878/radarr/api/v3/release?apikey=radarr-key",
        json={"id": 9},
        status_code=201,
    )
    with ArrClient("http://radarr:7878/radarr", "radarr-key") as client:
        result = grab_radarr_release(client, movie_id=42, guid="g1")
    assert result["ok"] is True
    assert result["app"] == "radarr"
