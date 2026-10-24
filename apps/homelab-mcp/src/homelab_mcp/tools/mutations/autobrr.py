from __future__ import annotations

import time
from typing import Any

import httpx
from homelab_workers.shared.autobrr import AutobrrClient

from homelab_mcp.audit_log import write_audit_record
from homelab_mcp.config import Settings
from homelab_mcp.envelope import error_response, ok_response
from homelab_mcp.mutation_gate import (
    canonical_filter_disable,
    canonical_filter_enable,
    canonical_irc_restart,
    require_tier1,
    require_tier2,
)

TOOL_FILTER_SET_ENABLED = "autobrr_filter_set_enabled"
TOOL_IRC_RESTART = "autobrr_irc_restart"


def _client(settings: Settings) -> AutobrrClient:
    return AutobrrClient(settings.autobrr_url, settings.autobrr_api_key)


def _gate_error_response(
    tool: str,
    *,
    settings: Settings,
    tier: int,
    started: float,
    gate_err: dict[str, str],
) -> str:
    duration_ms = int((time.perf_counter() - started) * 1000)
    write_audit_record(
        audit_path=settings.audit_path,
        tool=tool,
        tier=tier,
        ok=False,
        duration_ms=duration_ms,
        error_code=gate_err["code"],
        detail={"hint": gate_err.get("hint", "")},
    )
    return error_response(
        tool,
        code=gate_err["code"],
        message=gate_err["message"],
        hint=gate_err.get("hint", ""),
    )


def _upstream_error(tool: str, started: float, exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return error_response(
            tool,
            code="UPSTREAM_TIMEOUT",
            message="autobrr request timed out",
            hint="Verify autobrr is reachable on homelab_net.",
        )
    if isinstance(exc, ValueError):
        return error_response(tool, code="NOT_FOUND", message=str(exc))
    _ = started
    return error_response(
        tool,
        code="UPSTREAM_ERROR",
        message=f"autobrr request failed: {exc}",
        hint="Check AUTOBRR_API_KEY and AUTOBRR_INTERNAL_URL.",
    )


def filter_set_enabled(
    settings: Settings,
    *,
    filter_name: str,
    enabled: bool,
    confirm: bool = False,
    expected_action: str | None = None,
) -> str:
    started = time.perf_counter()
    tool = TOOL_FILTER_SET_ENABLED
    if not settings.enable_mutations:
        return error_response(
            tool,
            code="MUTATIONS_DISABLED",
            message="Mutation tools are disabled",
            hint="Set HOMELAB_MCP_ENABLE_MUTATIONS=1 and restart homelab-mcp.",
        )

    if enabled:
        tier = 2
        gate_err = require_tier2(confirm=confirm)
    else:
        tier = 1
        gate_err = require_tier1(
            expected=canonical_filter_disable(filter_name),
            provided=expected_action,
        )

    if gate_err:
        return _gate_error_response(tool, settings=settings, tier=tier, started=started, gate_err=gate_err)

    try:
        result = _client(settings).set_filter_enabled(filter_name, enabled=enabled)
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=True,
            duration_ms=duration_ms,
            detail={
                "filter": filter_name,
                "enabled": enabled,
                "expected_action": canonical_filter_enable(filter_name) if enabled else canonical_filter_disable(filter_name),
            },
        )
        data: dict[str, Any] = {"mutation": True, "applied": result}
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        err = _upstream_error(tool, started, exc)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return err


def irc_restart(
    settings: Settings,
    *,
    network: str,
    confirm: bool = False,
) -> str:
    started = time.perf_counter()
    tool = TOOL_IRC_RESTART
    tier = 2
    if not settings.enable_mutations:
        return error_response(
            tool,
            code="MUTATIONS_DISABLED",
            message="Mutation tools are disabled",
            hint="Set HOMELAB_MCP_ENABLE_MUTATIONS=1 and restart homelab-mcp.",
        )

    gate_err = require_tier2(confirm=confirm)
    if gate_err:
        return _gate_error_response(tool, settings=settings, tier=tier, started=started, gate_err=gate_err)

    try:
        result = _client(settings).restart_irc_network(network)
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=True,
            duration_ms=duration_ms,
            detail={
                "network": network,
                "expected_action": canonical_irc_restart(network),
            },
        )
        return ok_response(tool, {"mutation": True, "applied": result}, duration_ms=duration_ms)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return _upstream_error(tool, started, exc)
