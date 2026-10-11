from __future__ import annotations

from homelab_workers.shared.prowlarr import ProwlarrClient
from homelab_workers.shared.prowlarr_releases import search_prowlarr_releases


def test_search_prowlarr_option_marks_freeleech(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://prowlarr:9696/prowlarr/api/v1/search?query=Movie&limit=10&type=search",
        json=[
            {
                "title": "FL Release",
                "seeders": 5,
                "size": 1000,
                "guid": "g1",
                "indexerId": 1,
                "indexerFlags": ["freeleech"],
                "rejections": ["Quality rejected"],
            },
        ],
    )
    with ProwlarrClient("http://prowlarr:9696/prowlarr", "key") as client:
        result = search_prowlarr_releases(client, query="Movie")
    assert result["ok"] is True
    opt = result["options"][0]
    assert opt["indexer_flags"] == ["freeleech"]
    assert opt["is_freeleech"] is True
    assert opt["rejections"] == ["Quality rejected"]
    assert "release" not in opt
