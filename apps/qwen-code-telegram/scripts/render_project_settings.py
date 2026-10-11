#!/usr/bin/env python3
"""Render /run/qwen-code/project-settings.json from environment for Qwen v0.21.15.

This helper runs AS ROOT inside the entrypoint before drop-priv, writes
the runtime settings JSON, then entrypoint chowns it qwen:qwen.

Required env (fail-closed):
  OPENAI_API_KEY                    — source VLLM_API_KEY secret (hydrated from file)
  TELEGRAM_BOT_TOKEN                — Telegram bot token (hydrated from secret file)
  MCP_API_TOKEN                     — MCP bearer token for homelab-mcp-telegram (hydrated)
  QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS — comma-whitespace separated list of numeric chat ids
  QWEN_CODE_TELEGRAM_ROLE           — "trusted" or "owner"

Hardcoded (compose-internal, not user-configurable):
  baseURL = http://qwen-serve:18020/v1
  models / defaultModel = qwen3.8-27b
  MCP httpUrl = http://homelab-mcp-telegram:8090/mcp

Output JSON shape (written only to file path arg):
{
  "modelProviders": {
    "openai": [
      {
        "id": "qwen3.8-27b",
        "name": "qwen3.8-27b",
        "baseUrl": "http://qwen-serve:18020/v1",
        "envKey": "OPENAI_API_KEY",
        "generationConfig": {"contextWindowSize": 65536}
      }
    ]
  },
  "security": {
    "auth": {
      "selectedType": "openai"
    }
  },
  "model": {
    "name": "qwen3.8-27b"
  },
  "defaultModel": "qwen3.8-27b",
  "channels": {
    "homelab-telegram": {
      "type": "telegram",
      "token": "...",
      "senderPolicy": "allowlist",
      "allowedUsers": ["100","200","-500"],
      "sessionScope": "user",
      "cwd": "/workspace",
      "groupPolicy": "disabled",
      "dispatchMode": "followup"
    }
  },
  "mcpServers": {
    "homelab": {
      "httpUrl": "http://homelab-mcp-telegram:8090/mcp",
      "headers": {"Authorization": "Bearer <token>"},
      "trust": false,
      "includeTools": ["...(17 readonly or 17+4=21 acquire)..."],
      "alwaysLoadTools": true
    }
  },
  "mcp": {
    "allowed": ["homelab"]
  }
}

Failure modes:
  exit 2 — missing required env
  exit 3 — allowedUsers parse fails (non-numeric tokens after trim+split)
  exit 4 — invalid TELEGRAM_MCP_MODE value
  exit 5 — write IO error
  exit 0 — success (writes file)
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

BASE_URL = "http://qwen-serve:18020/v1"
MODEL_NAME = "qwen3.8-27b"
MCP_HTTP_URL = os.environ.get(
    "QWEN_CODE_TELEGRAM_MCP_URL", "http://homelab-mcp-telegram:8090/mcp"
)
# Bounds the client's assumed context window so auto-compaction fires near
# the ~50% operational target. Unset, v0.21.15 assumes a 200k window and
# never compacts in operational range (see DEV-MERGE-PLAN A3).
CONTEXT_WINDOW_SIZE = 65536
PERMITTED_CAPABILITIES = frozenset(
    {
        "read",
        "trusted-acquisition",
        "trusted-single-control",
        "owner-mutation",
        "owner-admin",
    }
)
TRUSTED_ALLOWED_CAPABILITIES = frozenset(
    {"read", "trusted-acquisition", "trusted-single-control"}
)
_SUCCESS_EVIDENCE_STATUSES = frozenset({"confirmed", "ok", "success"})
_UNCERTAIN_EVIDENCE_STATUSES = frozenset({"unknown", "unavailable", "error"})
_SECRET_OR_PATH_RE = re.compile(
    r"(?:\b(?:api[_ -]?key|token|password|cookie|secret)\s*[:=]|"
    r"\bbearer\s+|/(?:run/secrets|home/jon|homelab)(?:/|\b))",
    re.IGNORECASE,
)
_POSITIVE_FALSE_PREMISE_RE = re.compile(r"\b(?:is|are)\s+(?:available|present|in (?:the )?library)\b", re.IGNORECASE)


def parse_response_contract(text: str) -> dict[str, object]:
    """Parse a Telegram response into evidence ledger fields and metrics.

    The contract allows optional markdown fences around the ledger.
    """
    raw_lines = text.splitlines()
    cleaned_lines = []
    for line in raw_lines:
        trimmed = line.strip()
        if trimmed in {"```", "```markdown", "```text"}:
            continue
        if trimmed.startswith("```"):
            trimmed = trimmed[3:].strip()
        if trimmed.endswith("```"):
            trimmed = trimmed[:-3].strip()
        cleaned_lines.append(trimmed)

    nonblank_lines = [line for line in cleaned_lines if line]
    fields: dict[str, str] = {}
    labels = (("Result:", "result"), ("Verified:", "verified"), ("Checked:", "checked"))
    for line, (label, key) in zip(nonblank_lines[:3], labels):
        if line.startswith(label):
            fields[key] = line[len(label) :].strip()
    # Keep extracting fields for diagnostics even when the positional contract
    # is invalid; validation below still rejects the malformed layout.
    for line in nonblank_lines[3:]:
        for label, key in labels:
            if line.startswith(label) and key not in fields:
                fields[key] = line[len(label) :].strip()
                break

    verified_raw = fields.get("verified", "")
    verified = [] if verified_raw.lower() in {"", "none"} else [
        source.strip() for source in verified_raw.split(",") if source.strip()
    ]
    has_three_ledger = (
        len(nonblank_lines) >= 3
        and nonblank_lines[0].startswith("Result:")
        and nonblank_lines[1].startswith("Verified:")
        and nonblank_lines[2].startswith("Checked:")
    )
    return {
        "result": fields.get("result"),
        "verified": verified,
        "checked": fields.get("checked"),
        "has_result": "result" in fields,
        "has_verified": "verified" in fields,
        "has_checked": "checked" in fields,
        "ledger_is_strict": has_three_ledger,
        "characters": len(text),
        "nonblank_lines": len(nonblank_lines),
        "list_items": sum(bool(re.match(r"^\s*(?:[-*]|\d+[.)])\s+", line)) for line in raw_lines),
    }


def _evidence_value(record: object, field: str, default: object) -> object:
    return getattr(record, field, default)


def _contains_raw_json(text: str) -> bool:
    """Detect JSON objects/arrays anywhere, including after a valid ledger."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", text):
        try:
            value, _end = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return True
    return bool(re.search(r"```\s*(?:json)?\s*[\[{]", text, re.IGNORECASE))


def validate_response_contract(text: str, evidence: Sequence[object], category: str) -> list[str]:
    """Return policy violations for a model answer and this turn's MCP evidence.

    The validator deliberately works against the EvidenceRecord attribute contract
    rather than importing homelab-mcp: the renderer is packaged independently.
    """
    parsed = parse_response_contract(text)
    errors: list[str] = []
    result = str(parsed["result"] or "")
    verified = parsed["verified"]
    checked = str(parsed["checked"] or "")

    for label, key in (("Result", "has_result"), ("Verified", "has_verified"), ("Checked", "has_checked")):
        if not parsed[key]:
            errors.append(f"missing {label} ledger field")
    if not parsed["ledger_is_strict"] and category.lower() != "search-expanded":
        errors.append("ledger must start exactly with Result, Verified, Checked and contain no extra prose")

    if checked:
        try:
            normalized_checked = checked[:-1] + "+00:00" if checked.endswith("Z") else checked
            checked_at = datetime.fromisoformat(normalized_checked)
        except ValueError:
            checked_at = None
        if (
            checked_at is None
            or checked_at.tzinfo is None
            or checked_at.utcoffset() is None
            or checked_at.utcoffset() != timedelta(0)
        ):
            errors.append("Checked must be an ISO-8601 UTC timestamp")

    if evidence is not None:
        successful = [
            record
            for record in evidence
            if str(_evidence_value(record, "status", "")).lower() in _SUCCESS_EVIDENCE_STATUSES
        ]
        uncertain = [
            record
            for record in evidence
            if str(_evidence_value(record, "status", "")).lower() in _UNCERTAIN_EVIDENCE_STATUSES
        ]
        successful_sources = {str(_evidence_value(record, "source", "")) for record in successful}
        evidence_times = {str(_evidence_value(record, "checked_at", "")) for record in evidence}

        if any(source not in successful_sources for source in verified):
            errors.append("Verified contains a source without successful same-turn evidence")
        if verified and checked not in {str(_evidence_value(record, "checked_at", "")) for record in successful}:
            errors.append("Checked must match successful same-turn evidence")
        elif not verified and evidence and checked not in evidence_times:
            errors.append("Checked must match same-turn evidence")

        if not successful and (uncertain or not evidence):
            uncertain_result = result.lower()
            if "unknown" not in uncertain_result and "unavailable" not in uncertain_result:
                errors.append("Unknown or Unavailable must be stated when evidence is not confirmed")
            if not re.search(r"\b(?:retry|check|try|contact|inspect|run)\b", uncertain_result):
                errors.append("Unknown or Unavailable answers must include a useful next action")

        false_evidence = any(
            any(value is False for key, value in dict(_evidence_value(record, "facts", {})).items() if key in {"in_library", "present", "exists"})
            for record in successful
        )
        if false_evidence and _POSITIVE_FALSE_PREMISE_RE.search(result) and not re.search(r"\bnot\b|\bno\b", result, re.IGNORECASE):
            errors.append("false premise must be corrected from same-turn evidence")

    if category.lower() in {"mutation", "mutate"} and successful:
        facts = [dict(_evidence_value(record, "facts", {})) for record in successful]
        mutation_indices = [
            index
            for index, item in enumerate(facts)
            if item.get("mutation") is True or ("action" in item and not {"pre_state", "before", "post_state", "after", "state_after"} & set(item))
        ]
        pre_indices = [index for index, item in enumerate(facts) if {"pre_state", "before"} & set(item)]
        post_indices = [index for index, item in enumerate(facts) if {"post_state", "after", "state_after"} & set(item)]

        # Legacy envelopes may expose only a state on the pre/post reads. Infer
        # those positions only around an explicitly marked mutation record.
        if mutation_indices:
            mutation_index = mutation_indices[0]
            if not pre_indices and mutation_index > 0:
                pre_indices = [mutation_index - 1]
            if not post_indices and mutation_index + 1 < len(facts):
                post_indices = [mutation_index + 1]
        pre_index = pre_indices[0] if pre_indices else None
        post_index = post_indices[0] if post_indices else None

        targets = {str(item["target"]) for item in facts if item.get("target") is not None}
        actions = {str(item["action"]) for item in facts if item.get("action") is not None}
        if len(targets) > 1 or len(actions) > 1 or not mutation_indices:
            errors.append("successful mutation requires target/action-linked evidence")
        if pre_index is None:
            errors.append("successful mutation requires pre-state evidence")
        if post_index is None:
            errors.append("successful mutation requires post-state evidence")
        if pre_index is not None and post_index is not None and not (
            pre_index < mutation_indices[0] < post_index
        ):
            errors.append("successful mutation evidence must be ordered pre-state, action, post-state")
        has_audit = (
            post_index is not None
            and any("audit" in key or key == "actor" for key in facts[post_index])
        )
        if not has_audit:
            errors.append("successful mutation requires actor-tagged audit evidence")

    if _SECRET_OR_PATH_RE.search(text):
        errors.append("response leaks a secret marker or internal path")
    if _contains_raw_json(text):
        errors.append("response must not expose raw JSON")

    normalized_category = category.lower()
    max_chars, max_lines = (400, 8) if normalized_category in {"simple", "command"} else (900, 14)
    if normalized_category.startswith(("search", "list")):
        max_chars, max_lines = 1200, 14
    if int(parsed["characters"]) > max_chars:
        errors.append(f"response exceeds {max_chars}-character limit")
    if int(parsed["nonblank_lines"]) > max_lines:
        errors.append(f"response exceeds {max_lines}-line limit")
    if normalized_category in {"search", "list"} and int(parsed["list_items"]) > 5:
        errors.append("search/list response exceeds 5 displayed items")

    return errors

def validate_role_tool_artifact(artifact: object) -> dict[str, list[str]]:
    """Validate the shared role artifact using the MCP server's policy."""
    if not isinstance(artifact, dict) or set(artifact) != {"capabilities", "roles"}:
        raise ValueError("mcp-role-tools.json must contain capabilities and roles")

    capabilities = artifact["capabilities"]
    roles = artifact["roles"]
    if (
        not isinstance(capabilities, dict)
        or not isinstance(roles, dict)
        or set(roles) != {"trusted", "owner"}
        or not all(
            isinstance(tool, str)
            and tool
            and isinstance(capability, str)
            and capability
            for tool, capability in capabilities.items()
        )
    ):
        raise ValueError("mcp-role-tools.json must define trusted and owner roles")

    invalid_capabilities = set(capabilities.values()) - PERMITTED_CAPABILITIES
    if invalid_capabilities:
        raise ValueError(
            "mcp-role-tools.json has invalid capability values: "
            + ", ".join(sorted(invalid_capabilities))
        )

    registry: dict[str, list[str]] = {}
    for role, tools in roles.items():
        if (
            not isinstance(tools, list)
            or not all(isinstance(tool, str) and tool for tool in tools)
            or len(tools) != len(set(tools))
            or tools != sorted(tools)
        ):
            raise ValueError(f"mcp-role-tools.json has invalid {role} tools")
        registry[role] = tools
    if not set(registry["trusted"]) < set(registry["owner"]):
        raise ValueError("trusted role tools must be a strict owner subset")
    if set(registry["owner"]) != set(capabilities):
        raise ValueError("owner role tools must exactly match classified tools")

    trusted_capabilities = {
        capabilities[name] for name in registry["trusted"]
    }
    disallowed_trusted = trusted_capabilities - TRUSTED_ALLOWED_CAPABILITIES
    if disallowed_trusted:
        raise ValueError(
            "trusted role has disallowed capabilities: "
            + ", ".join(sorted(disallowed_trusted))
        )
    return registry


def _load_role_tool_registry() -> dict[str, list[str]]:
    candidates = (
        Path(__file__).parents[1] / "config" / "mcp-role-tools.json",
        Path(__file__).with_name("mcp-role-tools.json"),
    )
    artifact_path = next((path for path in candidates if path.is_file()), None)
    if artifact_path is None:
        die(4, "authoritative mcp-role-tools.json artifact is missing")
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(4, f"invalid mcp-role-tools.json artifact: {exc}")
    try:
        return validate_role_tool_artifact(artifact)
    except ValueError as exc:
        print(f"[render_settings] FAIL: {exc}", file=sys.stderr)
        raise SystemExit(4) from exc


_ROLE_TOOL_REGISTRY = _load_role_tool_registry()
# Compatibility exports for policy tests; both are derived from the artifact.
READONLY_TOOLS = _ROLE_TOOL_REGISTRY["trusted"]
ACQUIRE_APPENDS: list[str] = []

_NUMERIC_RE = re.compile(r"^-?\d+$")


def die(code: int, msg: str) -> None:
    print(f"[render_settings] FAIL: {msg}", file=sys.stderr)
    sys.exit(code)


def required(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        die(2, f"required env {name} is empty or unset")
    return val


def parse_allowed_users(raw: str) -> list[str]:
    tokens = [t.strip() for t in raw.split(",")]
    tokens = [t for t in tokens if t]
    if not tokens:
        die(3, "QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS empty after splitting comma list")
    out: list[str] = []
    for t in tokens:
        if not _NUMERIC_RE.match(t):
            die(
                3,
                f"QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS token {t!r} is not a numeric chat id",
            )
        out.append(t)
    return out


def build_include_tools(role: str) -> list[str]:
    try:
        return list(_ROLE_TOOL_REGISTRY[role])
    except KeyError:
        die(4, f"QWEN_CODE_TELEGRAM_ROLE must be 'trusted' or 'owner'; got {role!r}")
        return []  # unreachable


def build_settings() -> dict:
    required("OPENAI_API_KEY")
    required("TELEGRAM_BOT_TOKEN")
    required("MCP_API_TOKEN")
    allowed_raw = required("QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS")
    role = os.environ.get("QWEN_CODE_TELEGRAM_ROLE", "trusted")

    allowed_users = parse_allowed_users(allowed_raw)
    include_tools = build_include_tools(role)

    return {
        "modelProviders": {
            "openai": [
                {
                    "id": MODEL_NAME,
                    "name": MODEL_NAME,
                    "baseUrl": BASE_URL,
                    "envKey": "OPENAI_API_KEY",
                    "generationConfig": {
                        "contextWindowSize": CONTEXT_WINDOW_SIZE,
                    },
                }
            ]
        },
        "security": {
            "auth": {
                "selectedType": "openai",
            }
        },
        "model": {
            "name": MODEL_NAME,
        },
        "defaultModel": MODEL_NAME,
        "channels": {
            "homelab-telegram": {
                "type": "telegram",
                "token": "$TELEGRAM_BOT_TOKEN",
                "senderPolicy": "allowlist",
                "allowedUsers": allowed_users,
                "sessionScope": "user",
                "cwd": "/workspace",
                "groupPolicy": "disabled",
                "dispatchMode": "followup",
            }
        },
        "mcpServers": {
            "homelab": {
                "httpUrl": MCP_HTTP_URL,
                "headers": {
                    "Authorization": "Bearer $MCP_API_TOKEN",
                },
                "trust": False,
                "includeTools": include_tools,
                "alwaysLoadTools": True,
            }
        },
        "mcp": {
            "allowed": ["homelab"]
        },
    }


def main(out_path: str) -> None:
    settings = build_settings()
    p = Path(out_path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
    except OSError as e:
        die(5, f"write error to {out_path}: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            f"usage: {sys.argv[0]} /run/qwen-code/project-settings.json",
            file=sys.stderr,
        )
        sys.exit(2)
    main(sys.argv[1])
