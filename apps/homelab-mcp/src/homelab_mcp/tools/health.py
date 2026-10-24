from __future__ import annotations

import time
from typing import Iterable

from homelab_mcp.catalog import V1_SERVICES, catalog_payload
from homelab_mcp.config import Settings
from homelab_mcp.envelope import ok_response
from homelab_mcp.health_probe import build_headers, probe_service

TOOL_COMPOSE_SERVICE_CATALOG = "homelab_compose_service_catalog"
TOOL_SERVICES_HEALTH = "homelab_services_health"


def homelab_compose_service_catalog() -> str:
    started = time.perf_counter()
    data = catalog_payload()
    duration_ms = int((time.perf_counter() - started) * 1000)
    return ok_response(TOOL_COMPOSE_SERVICE_CATALOG, data, duration_ms=duration_ms)


def homelab_services_health(settings: Settings, services: Iterable[str] | None = None) -> str:
    started = time.perf_counter()
    entries_by_name = {entry.name: entry for entry in V1_SERVICES}
    names = [entry.name for entry in V1_SERVICES] if services is None else list(services)
    results: list[dict[str, object]] = []
    summary = {"healthy_count": 0, "degraded_count": 0, "down_count": 0, "skipped_count": 0}

    for name in names:
        entry = entries_by_name.get(name)
        if entry is None:
            results.append(
                {
                    "service": name,
                    "reachable": False,
                    "http_status": None,
                    "latency_ms": 0,
                    "status": "skipped",
                    "detail": "unknown service",
                }
            )
            summary["skipped_count"] += 1
            continue

        result = probe_service(
            name=entry.name,
            url=f"{entry.base_url.rstrip('/')}{entry.health_path}",
            headers=build_headers(entry, settings),
        )
        results.append(result)
        status = str(result.get("status") or "")
        if status == "healthy":
            summary["healthy_count"] += 1
        elif status == "degraded":
            summary["degraded_count"] += 1
        elif status == "down":
            summary["down_count"] += 1

    duration_ms = int((time.perf_counter() - started) * 1000)
    return ok_response(TOOL_SERVICES_HEALTH, {"results": results, "summary": summary}, duration_ms=duration_ms)
