from __future__ import annotations

from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.arr_indexers import arr_indexer_sync_health


def test_arr_indexer_sync_health_counts(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/indexer?apikey=radarr-key",
        json=[{"enable": True}, {"enable": False}],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/indexer?apikey=sonarr-key",
        json=[{"enable": True}],
    )
    with ArrClient("http://radarr:7878/radarr", "radarr-key") as radarr, ArrClient(
        "http://sonarr:8989/sonarr", "sonarr-key"
    ) as sonarr:
        payload = arr_indexer_sync_health(radarr=radarr, sonarr=sonarr)
    assert payload["radarr"]["enabled_indexers"] == 1
    assert payload["sonarr"]["enabled_indexers"] == 1
    assert payload["healthy"] is True


def test_arr_indexer_sync_health_counts_prowlarr_sync_flags(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/indexer?apikey=radarr-key",
        json=[
            {
                "enable": None,
                "enableRss": True,
                "enableAutomaticSearch": True,
                "enableInteractiveSearch": True,
            },
            {
                "enable": None,
                "enableRss": False,
                "enableAutomaticSearch": False,
                "enableInteractiveSearch": False,
            },
        ],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/indexer?apikey=sonarr-key",
        json=[
            {
                "enable": None,
                "enableRss": True,
                "enableAutomaticSearch": False,
                "enableInteractiveSearch": False,
            }
        ],
    )
    with ArrClient("http://radarr:7878/radarr", "radarr-key") as radarr, ArrClient(
        "http://sonarr:8989/sonarr", "sonarr-key"
    ) as sonarr:
        payload = arr_indexer_sync_health(radarr=radarr, sonarr=sonarr)
    assert payload["radarr"]["enabled_indexers"] == 1
    assert payload["sonarr"]["enabled_indexers"] == 1
    assert payload["healthy"] is True


def test_arr_indexer_sync_health_zero_indexers(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://radarr:7878/radarr/api/v3/indexer?apikey=radarr-key",
        json=[{"enable": False}],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/indexer?apikey=sonarr-key",
        json=[{"enable": True}],
    )
    with ArrClient("http://radarr:7878/radarr", "radarr-key") as radarr, ArrClient(
        "http://sonarr:8989/sonarr", "sonarr-key"
    ) as sonarr:
        payload = arr_indexer_sync_health(radarr=radarr, sonarr=sonarr)
    assert payload["healthy"] is False
    assert payload["hint"] is not None
