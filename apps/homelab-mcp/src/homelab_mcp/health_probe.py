from __future__ import annotations

import time
from typing import Any

import httpx

from homelab_mcp.catalog import ServiceEntry
from homelab_mcp.config import Settings
from homelab_mcp.redaction import redact_secret_string


def probe_service(
    *, name: str, url: str, timeout_seconds: float = 10.0, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(url, headers=headers or {})
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code < 400:
            return {
                "service": name,
                "reachable": True,
                "http_status": response.status_code,
                "latency_ms": latency_ms,
                "status": "healthy",
                "detail": "ok",
            }
        return {
            "service": name,
            "reachable": True,
            "http_status": response.status_code,
            "latency_ms": latency_ms,
            "status": "degraded",
            "detail": f"HTTP {response.status_code}",
        }
    except httpx.TimeoutException:
        return {
            "service": name,
            "reachable": False,
            "http_status": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "status": "down",
            "detail": "timeout",
        }
    except httpx.HTTPError as exc:
        return {
            "service": name,
            "reachable": False,
            "http_status": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "status": "down",
            "detail": redact_secret_string(str(exc)),
        }


def build_headers(entry: ServiceEntry, settings: Settings) -> dict[str, str]:
    if entry.auth_header == "X-Api-Key":
        key_by_service = {
            "sonarr": settings.sonarr_api_key,
            "radarr": settings.radarr_api_key,
            "prowlarr": settings.prowlarr_api_key,
        }
        key = key_by_service.get(entry.name, "")
        return {"X-Api-Key": key} if key else {}
    if entry.auth_header == "Authorization" and settings.media_agent_token:
        return {"Authorization": f"Bearer {settings.media_agent_token}"}
    if entry.auth_header == "X-API-Token" and settings.autobrr_api_key:
        return {"X-API-Token": settings.autobrr_api_key}
    return {}
