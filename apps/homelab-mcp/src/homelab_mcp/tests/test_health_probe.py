from __future__ import annotations

import httpx

from homelab_mcp.catalog import ServiceEntry
from homelab_mcp.catalog import catalog_payload
from homelab_mcp.health_probe import build_headers, probe_service


def test_probe_service_ok(httpx_mock) -> None:
    httpx_mock.add_response(url="http://jellyfin:8096/health", status_code=200)
    result = probe_service(name="jellyfin", url="http://jellyfin:8096/health", timeout_seconds=5)
    assert result["status"] == "healthy"
    assert result["reachable"] is True
    assert result["http_status"] == 200


def test_probe_service_degraded_on_http_error_status(httpx_mock) -> None:
    httpx_mock.add_response(url="http://caddy:2019/config/", status_code=503)
    result = probe_service(name="caddy", url="http://caddy:2019/config/")
    assert result["status"] == "degraded"
    assert result["reachable"] is True
    assert result["http_status"] == 503


def test_probe_service_timeout(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
    result = probe_service(name="plex", url="http://plex:32400/identity")
    assert result["status"] == "down"
    assert result["reachable"] is False
    assert result["http_status"] is None


def test_probe_service_redacts_secret_url_from_error(httpx_mock) -> None:
    httpx_mock.add_exception(
        httpx.ConnectError(
            "failed for https://example.invalid/api?apikey=SECRET&ok=1",
        )
    )
    result = probe_service(name="example", url="http://example.invalid/")
    assert result["status"] == "down"
    assert "SECRET" not in result["detail"]
    assert "apikey=[redacted]" in result["detail"]


def test_build_headers_x_api_key(mcp_settings) -> None:
    headers = build_headers(
        ServiceEntry("sonarr", "media.yml", "http://sonarr:8989/sonarr", "/api/v3/system/status", auth_header="X-Api-Key"),
        mcp_settings,
    )
    assert headers == {"X-Api-Key": "sonarr-key"}


def test_build_headers_bearer(mcp_settings) -> None:
    headers = build_headers(
        ServiceEntry(
            "media-agent",
            "media.yml",
            "http://media-agent:8000",
            "/internal/media-agent/v1/health",
            auth_header="Authorization",
        ),
        mcp_settings,
    )
    assert headers == {"Authorization": "Bearer media-token"}


def test_build_headers_bearer_empty(mcp_settings) -> None:
    import dataclasses
    no_token_settings = dataclasses.replace(mcp_settings, media_agent_token="")
    headers = build_headers(
        ServiceEntry(
            "media-agent",
            "media.yml",
            "http://media-agent:8000",
            "/internal/media-agent/v1/health",
            auth_header="Authorization",
        ),
        no_token_settings,
    )
    assert headers == {}


def test_catalog_excludes_retired_services() -> None:
    services = {service["name"]: service for service in catalog_payload()["services"]}
    assert "codegraphcontext" not in services
    assert "ollama" not in services
    assert "caddy" in services
    assert "jellyfin" in services
    assert "plex" in services
    assert "qbittorrent" in services

