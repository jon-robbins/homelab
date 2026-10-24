"""Shared envelope for gated mutations: switch check, gate, audit, and error mapping."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from homelab_mcp.audit_log import write_audit_record
from homelab_mcp.config import Settings
from homelab_mcp.envelope import error_response, ok_response
from homelab_mcp.file_paths import PathRejected
from homelab_mcp.mutation_gate import require_tier1, require_tier2


class Refused(Exception):
    """A mutation precondition failed; carries the envelope error code."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _classify(exc: Exception) -> Refused:
    if isinstance(exc, Refused):
        return exc
    if isinstance(exc, PathRejected):
        return Refused("PATH_REJECTED", str(exc))
    if isinstance(exc, httpx.TimeoutException):
        return Refused("UPSTREAM_TIMEOUT", "upstream request timed out")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        code = "NOT_FOUND" if status == 404 else "UPSTREAM_ERROR"
        return Refused(code, f"upstream returned HTTP {status}")
    if isinstance(exc, httpx.HTTPError):
        return Refused("UPSTREAM_ERROR", f"upstream request failed: {exc}")
    if isinstance(exc, OSError):
        return Refused("FILESYSTEM_ERROR", str(exc))
    raise exc


def run_mutation(
    settings: Settings,
    *,
    tool: str,
    tier: int,
    gate: Callable[[], str | bool],
    provided: str | bool | None,
    apply: Callable[[], dict[str, Any]],
) -> str:
    """Tier 1 compares ``provided`` against the computed expected_action; tier 2 needs confirm."""
    started = time.perf_counter()
    if not settings.enable_mutations:
        return error_response(
            tool,
            code="MUTATIONS_DISABLED",
            message="Mutation tools are disabled",
            hint="Set HOMELAB_MCP_ENABLE_MUTATIONS=1 and restart homelab-mcp.",
        )
    detail: dict[str, Any] = {}
    try:
        computed = gate()
        if tier == 1:
            failure = require_tier1(expected=str(computed), provided=provided)  # type: ignore[arg-type]
            detail["expected_action"] = computed
        else:
            failure = require_tier2(confirm=provided is True)
        if failure:
            raise Refused(failure["code"], failure["message"], failure.get("hint", ""))
        applied = apply()
    except Exception as exc:  # noqa: BLE001 - mapped to an envelope by _classify
        refused = _classify(exc)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=False,
            duration_ms=_ms(started),
            error_code=refused.code,
        )
        return error_response(tool, code=refused.code, message=str(refused), hint=refused.hint)
    write_audit_record(
        audit_path=settings.audit_path,
        tool=tool,
        tier=tier,
        ok=True,
        duration_ms=_ms(started),
        detail={**detail, **applied},
    )
    return ok_response(tool, {"mutation": True, "applied": applied}, duration_ms=_ms(started))
