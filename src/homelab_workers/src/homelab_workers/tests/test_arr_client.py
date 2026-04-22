from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from homelab_workers.arr_retry.client import ArrClient, read_api_key_from_config_xml


def test_arr_client_get_adds_api_path_and_params(httpx_mock) -> None:
    httpx_mock.add_response(method="GET", json={"records": [{"id": 1}]})

    with ArrClient("http://sonarr:8989", "test-key") as client:
        payload = client.get("queue", {"page": 2})

    assert payload["records"][0]["id"] == 1
    request = httpx_mock.get_requests()[0]
    assert request.url.path == "/api/v3/queue"
    assert request.url.params["apikey"] == "test-key"
    assert request.url.params["page"] == "2"


def test_arr_client_post_sends_payload_and_returns_json(httpx_mock) -> None:
    httpx_mock.add_response(method="POST", json={"id": 33})

    payload = {"name": "MoviesSearch", "movieIds": [99]}
    with ArrClient("http://radarr:7878", "radarr-key") as client:
        response = client.post("/command", payload)

    assert response["id"] == 33
    request = httpx_mock.get_requests()[0]
    assert request.method == "POST"
    assert request.url.params["apikey"] == "radarr-key"
    assert request.content.decode("utf-8") == '{"name":"MoviesSearch","movieIds":[99]}'


def test_arr_client_delete_sends_request_and_returns_none(httpx_mock) -> None:
    httpx_mock.add_response(method="DELETE", status_code=200)

    with ArrClient("http://radarr:7878", "radarr-key") as client:
        result = client.delete("/queue/123", {"removeFromClient": "true"})

    assert result is None
    request = httpx_mock.get_requests()[0]
    assert request.method == "DELETE"
    assert request.url.params["removeFromClient"] == "true"
    assert request.url.params["apikey"] == "radarr-key"


def test_arr_client_get_raises_for_http_error(httpx_mock) -> None:
    httpx_mock.add_response(method="GET", status_code=500)

    with ArrClient("http://sonarr:8989", "test-key") as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.get("/queue")


def test_arr_client_context_manager_closes_http_client(httpx_mock) -> None:
    httpx_mock.add_response(method="GET", json={"appName": "Sonarr"})

    with ArrClient("http://sonarr:8989", "test-key") as client:
        assert client._client.is_closed is False
        client.get("/api/v3/system/status")

    assert client._client.is_closed is True


def test_read_api_key_from_config_xml_returns_key(sonarr_config_xml: Path) -> None:
    assert read_api_key_from_config_xml(sonarr_config_xml) == "test-api-key-123"


def test_read_api_key_from_config_xml_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_api_key_from_config_xml(tmp_path / "missing.xml") == ""
