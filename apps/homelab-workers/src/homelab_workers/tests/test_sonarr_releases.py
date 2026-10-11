from __future__ import annotations

from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.sonarr_releases import SonarrReleaseSearchConfig, search_sonarr_releases


def test_sonarr_release_search_no_active_indexers(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://sonarr:8989/sonarr/api/v3/indexer?apikey=sonarr-key",
        json=[{"enable": False}],
    )
    with ArrClient("http://sonarr:8989/sonarr", "sonarr-key") as client:
        result = search_sonarr_releases(
            client,
            query="Show",
            season=1,
            series_id=7,
            include_full_series_packs=False,
            config=SonarrReleaseSearchConfig(),
        )
    assert result["ok"] is False
    assert result["error"]["code"] == "NO_ACTIVE_INDEXERS"
