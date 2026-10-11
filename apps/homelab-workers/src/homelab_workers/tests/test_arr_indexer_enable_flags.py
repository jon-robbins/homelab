"""Sonarr/Radarr indexer rows use enable* flags, not a top-level enable bool."""

from __future__ import annotations

from homelab_workers.shared.arr import ArrClient, indexer_row_is_enabled


def test_indexer_row_is_enabled_sonarr_style_flags() -> None:
    assert indexer_row_is_enabled(
        {
            "enableRss": True,
            "enableAutomaticSearch": True,
            "enableInteractiveSearch": True,
        }
    )
    assert not indexer_row_is_enabled(
        {
            "enableRss": False,
            "enableAutomaticSearch": False,
            "enableInteractiveSearch": False,
        }
    )


def test_indexer_row_is_enabled_legacy_enable_bool() -> None:
    assert indexer_row_is_enabled({"enable": True})
    assert not indexer_row_is_enabled({"enable": False})


def test_indexers_enabled_count_sonarr_payload(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/indexer?apikey=sonarr-key",
        json=[
            {
                "enableRss": True,
                "enableAutomaticSearch": True,
                "enableInteractiveSearch": True,
            },
            {
                "enableRss": False,
                "enableAutomaticSearch": False,
                "enableInteractiveSearch": False,
            },
        ],
    )
    with ArrClient("http://sonarr:8989/sonarr", "sonarr-key") as sonarr:
        assert sonarr.indexers_enabled_count() == 1
