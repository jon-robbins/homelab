import json
import sys
from pathlib import Path
import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "apps" / "qwen-code-telegram" / "scripts"))
sys.path.insert(0, str(_ROOT / "apps" / "homelab-mcp" / "src"))

from telegram_registry import load_role_registry, registered_tool_names
from render_project_settings import validate_response_contract, parse_response_contract
from homelab_mcp.evidence import EvidenceRecord


_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "homelab_mcp"
    / "mcp_envelopes_fixture.json"
)


def test_registered_tools_match_role_artifact_exactly():
    registry = load_role_registry()
    capabilities = registry["capabilities"]
    roles = registry["roles"]
    registered = registered_tool_names()

    # All registered tools in server.py must match owner role and classified capabilities
    assert set(registered) == set(capabilities)
    assert set(registered) == set(roles["owner"])

    # Trusted role must be a strict subset of owner role
    assert set(roles["trusted"]) < set(roles["owner"])

    # Ensure every tool is non-empty string and has valid capability
    for tool_name, cap in capabilities.items():
        assert isinstance(tool_name, str) and tool_name
        assert cap in {"read", "trusted-acquisition", "trusted-single-control", "owner-mutation", "owner-admin"}


def test_mcp_envelopes_fixture_is_sanitized():
    raw_text = _FIXTURE_PATH.read_text(encoding="utf-8")
    data = json.loads(raw_text)

    # Must not contain secret paths or tokens
    assert "/run/secrets" not in raw_text
    assert "/home/" not in raw_text
    assert "Bearer " not in raw_text

    # Structure checks for categories
    assert "read" in data
    assert "search" in data
    assert "mutation" in data
    assert "error" in data

    for category, envelopes in data.items():
        for name, env in envelopes.items():
            assert "ok" in env
            assert "tool" in env
            assert "evidence" in env
            assert "meta" in env
            assert isinstance(env["evidence"], list)
            if env["ok"]:
                assert "data" in env
            else:
                assert "error" in env


def test_expected_evidence_validates_envelope_structure():
    data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    grab_env = data["mutation"]["radarr_release_grab"]

    evidence_records = [
        EvidenceRecord(
            source=ev["source"],
            checked_at=ev["checked_at"],
            status=ev["status"],
            facts=ev["facts"],
        )
        for ev in grab_env["evidence"]
    ]

    # Pre-state + mutation + post-state with audit
    records = [
        EvidenceRecord("radarr_queue_snapshot", checked_at="2026-08-30T12:00:00Z", status="confirmed", facts={"pre_state": "idle"}),
        evidence_records[0],
        EvidenceRecord("radarr_queue_snapshot", checked_at="2026-08-30T12:00:00Z", status="confirmed", facts={"post_state": "grabbed", "actor": "trusted"}),
    ]

    answer_text = "Result: Grab applied for Night of the Living Dead.\n\nVerified: radarr_release_grab\nChecked: 2026-08-30T12:00:00Z"
    errors = validate_response_contract(answer_text, records, "mutation")
    assert errors == []


def test_search_expanded_enforces_ledger_and_provider_constraints():
    checked_time = "2026-08-30T12:00:00Z"
    evidence_rec = [
        EvidenceRecord("prowlarr_indexer_search", checked_at=checked_time, status="confirmed", facts={"options": []})
    ]

    valid_expanded = (
        f"Result: Search completed with 2 results.\n\n"
        f"Verified: prowlarr_indexer_search\n"
        f"Checked: {checked_time}\n\n"
        "- Result 1: Night of the Living Dead (1968) [Pirate Bay]\n"
        "- Result 2: Night of the Living Dead (1968) 720p [Pirate Bay]"
    )
    assert validate_response_contract(valid_expanded, evidence_rec, "search-expanded") == []

    # Missing ledger in search-expanded must fail
    no_ledger_expanded = (
        "- Result 1: Night of the Living Dead (1968) [Pirate Bay]\n"
        "- Result 2: Night of the Living Dead (1968) 720p [Pirate Bay]"
    )
    errors = validate_response_contract(no_ledger_expanded, evidence_rec, "search-expanded")
    assert any("ledger" in e.lower() or "missing" in e.lower() for e in errors)
