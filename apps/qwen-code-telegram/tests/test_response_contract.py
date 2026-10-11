"""Regression contract for concise, evidence-led Telegram answers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "qwen-code-telegram" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "homelab-mcp" / "src"))

from homelab_mcp.evidence import EvidenceRecord
from render_project_settings import parse_response_contract, validate_response_contract


CHECKED = "2026-08-30T12:00:00Z"


def evidence(
    source: str = "plex_library_search", *, status: str = "confirmed", **facts: object
) -> EvidenceRecord:
    return EvidenceRecord(source=source, checked_at=CHECKED, status=status, facts=dict(facts))


def answer(result: str, verified: str = "plex_library_search", checked: str = CHECKED) -> str:
    return f"Result: {result}\n\nVerified: {verified}\nChecked: {checked}"


def test_parses_required_evidence_ledger_fields() -> None:
    parsed = parse_response_contract(answer("Movie is available."))

    assert parsed["result"] == "Movie is available."
    assert parsed["verified"] == ["plex_library_search"]
    assert parsed["checked"] == CHECKED


@pytest.mark.parametrize(
    "text, missing",
    [
        ("Verified: plex_library_search\nChecked: 2026-08-30T12:00:00Z", "Result"),
        ("Result: Available\nChecked: 2026-08-30T12:00:00Z", "Verified"),
        ("Result: Available\nVerified: plex_library_search", "Checked"),
    ],
)
def test_requires_each_evidence_ledger_field(text: str, missing: str) -> None:
    errors = validate_response_contract(text, [evidence()], "diagnostic")

    assert any(missing in error for error in errors)


def test_verified_sources_and_timestamp_must_be_current_turn_evidence() -> None:
    errors = validate_response_contract(
        answer("Movie is available.", "radarr_queue_snapshot", "2026-08-29T12:00:00Z"),
        [evidence()],
        "diagnostic",
    )

    assert any("same-turn" in error for error in errors)


@pytest.mark.parametrize("status", ["unknown", "unavailable"])
def test_unknown_or_unavailable_evidence_requires_uncertainty_and_next_action(status: str) -> None:
    errors = validate_response_contract(
        answer("Movie is missing.", "None"),
        [evidence(status=status)],
        "diagnostic",
    )

    assert any("Unknown or Unavailable" in error for error in errors)
    assert any("next action" in error for error in errors)

    valid = answer("Unavailable: Plex did not respond. Retry the Plex check.", "None")
    assert validate_response_contract(valid, [evidence(status=status)], "diagnostic") == []


def test_false_premise_is_corrected_from_current_evidence() -> None:
    errors = validate_response_contract(
        answer("The movie is present in Plex."),
        [evidence(in_library=False)],
        "diagnostic",
    )

    assert any("false premise" in error for error in errors)
    valid = answer("The movie is not present in Plex.")
    assert validate_response_contract(valid, [evidence(in_library=False)], "diagnostic") == []


def test_successful_mutation_requires_pre_post_and_audit_evidence() -> None:
    errors = validate_response_contract(
        answer("Paused the requested torrent.", "qbt_torrents_pause"),
        [evidence("qbt_torrents_pause", action="pause")],
        "mutation",
    )

    assert any("pre-state" in error for error in errors)
    assert any("post-state" in error for error in errors)
    assert any("audit" in error for error in errors)

    records = [
        evidence("qbt_torrent_detail", state="downloading"),
        evidence("qbt_torrents_pause", action="pause"),
        evidence("qbt_torrent_detail", state="paused", audit_event="actor=trusted"),
    ]
    valid = answer("Paused torrent abc; it is now paused.", "qbt_torrent_detail, qbt_torrents_pause")
    assert validate_response_contract(valid, records, "mutation") == []


@pytest.mark.parametrize(
    "unsafe",
    [
        "Result: token=secret-value\n\nVerified: None\nChecked: 2026-08-30T12:00:00Z",
        "Result: Read /run/secrets/MCP_API_TOKEN\n\nVerified: None\nChecked: 2026-08-30T12:00:00Z",
        'Result: {"ok":true,"data":{"secret":"x"}}\n\nVerified: None\nChecked: 2026-08-30T12:00:00Z',
    ],
)
def test_rejects_secret_path_leaks_and_raw_json(unsafe: str) -> None:
    errors = validate_response_contract(unsafe, [], "diagnostic")

    assert errors


@pytest.mark.parametrize(
    "category, text",
    [
        ("simple", answer("x" * 401, "None")),
        ("diagnostic", answer("x" * 901, "None")),
        ("simple", "Result: ok\n\nVerified: None\nChecked: 2026-08-30T12:00:00Z\n\n1\n2\n3\n4\n5\n6"),
    ],
)
def test_enforces_simple_and_diagnostic_response_limits(category: str, text: str) -> None:
    errors = validate_response_contract(text, [], category)

    assert any("limit" in error for error in errors)


def test_search_lists_are_bounded_unless_explicitly_expanded() -> None:
    items = "\n".join(f"- result {index}" for index in range(1, 7))
    errors = validate_response_contract(answer(f"Matches:\n{items}"), [evidence()], "search")

    assert any("5" in error for error in errors)
    assert validate_response_contract(answer(f"Matches:\n{items}"), [evidence()], "search-expanded") == []
