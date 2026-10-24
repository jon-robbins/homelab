from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from homelab_workers.shared.autobrr import (
    AutobrrClient,
    build_operational_summary,
    verify_recent_pushes_in_qbt,
)
from homelab_workers.shared.qbittorrent import QBittorrentClient

from homelab_mcp.catalog import V1_SERVICES
from homelab_mcp.config import Settings
from homelab_mcp.diagnostics.autobrr_pipeline import diagnose_autobrr_pipeline
from homelab_mcp.envelope import error_response, ok_response
from homelab_mcp.health_probe import build_headers, probe_service

TOOL_OPERATIONAL_SUMMARY = "autobrr_operational_summary"
TOOL_PIPELINE_DIAGNOSIS = "autobrr_pipeline_diagnosis"


def _client(settings: Settings) -> AutobrrClient:
    return AutobrrClient(settings.autobrr_url, settings.autobrr_api_key)


def _upstream_error(tool: str, started: float, exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return error_response(
            tool,
            code="UPSTREAM_TIMEOUT",
            message="autobrr request timed out",
            hint="Verify autobrr is reachable on homelab_net.",
        )
    _ = started
    return error_response(
        tool,
        code="UPSTREAM_ERROR",
        message=f"autobrr request failed: {exc}",
        hint="Check AUTOBRR_API_KEY and AUTOBRR_INTERNAL_URL.",
    )


def _probe_catalog_service(settings: Settings, name: str) -> dict[str, object]:
    entry = next((e for e in V1_SERVICES if e.name == name), None)
    if entry is None:
        return {
            "service": name,
            "reachable": False,
            "status": "skipped",
            "detail": "unknown service",
        }
    return probe_service(
        name=entry.name,
        url=f"{entry.base_url.rstrip('/')}{entry.health_path}",
        headers=build_headers(entry, settings),
    )


def operational_summary(settings: Settings, recent_limit: int = 10) -> str:
    started = time.perf_counter()
    recent_limit = max(1, min(recent_limit, 25))
    try:
        client = _client(settings)
        data = build_operational_summary(
            filters=client.filters(),
            irc_networks=client.irc_networks(),
            releases=client.releases(limit=100),
            tz_name=os.environ.get("TZ", "UTC"),
            recent_limit=recent_limit,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(TOOL_OPERATIONAL_SUMMARY, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(TOOL_OPERATIONAL_SUMMARY, started, exc)


def _run_checks(
    checks: Sequence[Callable[[], dict[str, Any]]],
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Run extension checks; a failing check is reported, never fatal to the diagnosis."""
    health: dict[str, Any] = {}
    causes: list[tuple[str, str]] = []
    for check in checks:
        try:
            result = check()
            health[str(result["name"])] = result.get("health") or {}
            causes.extend((str(code), str(rec)) for code, rec in result.get("causes") or [])
        except Exception as exc:
            name = getattr(check, "__name__", "extension_check")
            health[name] = {"reachable": False, "error": f"check failed: {exc}"}
    return health, causes


def pipeline_diagnosis(
    settings: Settings, *, checks: Sequence[Callable[[], dict[str, Any]]] = ()
) -> str:
    started = time.perf_counter()
    try:
        client = _client(settings)
        releases = client.releases(limit=100)
        summary = build_operational_summary(
            filters=client.filters(),
            irc_networks=client.irc_networks(),
            releases=releases,
            tz_name=os.environ.get("TZ", "UTC"),
            recent_limit=10,
        )
        autobrr_health = _probe_catalog_service(settings, "autobrr")
        qbt_health = _probe_catalog_service(settings, "qbittorrent")

        with QBittorrentClient(
            settings.qbt_url, settings.qbt_username, settings.qbt_password
        ) as qbt:
            qbt_torrents = qbt.torrents_info_raw()

        push_verify = verify_recent_pushes_in_qbt(
            releases,
            qbt_torrents=qbt_torrents,
            recent_limit=10,
        )
        extra_health, extra_causes = _run_checks(checks)
        diagnosis = diagnose_autobrr_pipeline(
            summary=summary,
            autobrr_health=autobrr_health,
            qbt_health=qbt_health,
            push_verify=push_verify,
            extra_causes=extra_causes,
        )
        data = {
            "summary": summary,
            "health": {
                "autobrr": {
                    "reachable": autobrr_health.get("reachable"),
                    "status": autobrr_health.get("status"),
                },
                "qbittorrent": {
                    "reachable": qbt_health.get("reachable"),
                    "status": qbt_health.get("status"),
                },
                **extra_health,
            },
            "push_verify": push_verify,
            "likely_causes": diagnosis["likely_causes"],
            "recommendations": diagnosis["recommendations"],
        }
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(TOOL_PIPELINE_DIAGNOSIS, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(TOOL_PIPELINE_DIAGNOSIS, started, exc)
