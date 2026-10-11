from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from homelab_mcp.evidence import compact_tool_result, make_evidence
from homelab_mcp.redaction import redact_for_response

_MAX_EVIDENCE_FIELDS = 25


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _success_evidence_facts(data: dict[str, Any]) -> dict[str, object]:
    fields = sorted(data)
    facts: dict[str, object] = {
        "field_count": len(fields),
        "fields": fields[:_MAX_EVIDENCE_FIELDS],
    }
    if len(fields) > _MAX_EVIDENCE_FIELDS:
        facts["fields_omitted"] = len(fields) - _MAX_EVIDENCE_FIELDS
    return facts


def ok_response(tool: str, data: dict[str, Any], *, duration_ms: int) -> str:
    shaped = compact_tool_result(tool, data)
    return json.dumps(
        {
            "ok": True,
            "tool": tool,
            "data": redact_for_response(shaped),
            "evidence": [
                make_evidence(
                    tool,
                    status="confirmed",
                    facts=_success_evidence_facts(shaped),
                ).to_dict()
            ],
            "meta": {"duration_ms": duration_ms, "generated_at": _now_iso()},
        },
        separators=(",", ":"),
    )


def error_response(tool: str, *, code: str, message: str, hint: str = "") -> str:
    error: dict[str, Any] = {"code": code, "message": redact_for_response(message)}
    if hint:
        error["hint"] = redact_for_response(hint)
    return json.dumps(
        {
            "ok": False,
            "tool": tool,
            "error": error,
            "evidence": [
                make_evidence(
                    tool,
                    status="unavailable",
                    facts={"error_code": code},
                ).to_dict()
            ],
            "meta": {"generated_at": _now_iso()},
        },
        separators=(",", ":"),
    )
