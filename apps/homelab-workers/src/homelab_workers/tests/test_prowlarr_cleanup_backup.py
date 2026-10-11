from homelab_workers.shared.prowlarr import ProwlarrClient


def test_prowlarr_cleanup_backup_collects_indexers_and_history(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://prowlarr:9696/prowlarr/api/v1/indexer",
        json=[{"id": 7, "name": "ExampleIndexer"}],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://prowlarr:9696/prowlarr/api/v1/history?pageSize=1000",
        json={"records": [{"id": 22, "sourceTitle": "Release"}]},
    )
    with ProwlarrClient("http://prowlarr:9696/prowlarr", "key") as client:
        backup = client.cleanup_backup()
    assert backup["available"] is True
    assert backup["indexers"][0]["name"] == "ExampleIndexer"
    assert backup["history"][0]["id"] == 22
    assert backup["history_deletion_supported"] is False


def test_prowlarr_capability_report_is_read_only(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://prowlarr:9696/prowlarr/api/v1/system/status",
        json={"version": "1.0"},
    )
    with ProwlarrClient("http://prowlarr:9696/prowlarr", "key") as client:
        report = client.api_capability_report()
    assert report["status"]["version"] == "1.0"
    assert report["history_deletion_supported"] is False
