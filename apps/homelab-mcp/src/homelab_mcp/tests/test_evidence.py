from __future__ import annotations

from homelab_mcp.evidence import EvidenceRecord, compact_tool_result, make_evidence


def test_make_evidence_has_source_status_timestamp_and_redacted_facts() -> None:
    evidence = make_evidence(
        "plex",
        status="confirmed",
        facts={"title": "Example", "token": "secret"},
    )

    assert isinstance(evidence, EvidenceRecord)
    assert evidence.source == "plex"
    assert evidence.status == "confirmed"
    assert evidence.checked_at.endswith("Z")
    assert evidence.facts == {"title": "Example", "token": "[redacted]"}


def test_compact_tool_result_preserves_absent_and_unknown_values() -> None:
    shaped = compact_tool_result(
        "plex_library_search",
        {"matches": [{"title": "Show", "file_paths": None}, {"title": "Movie"}]},
    )

    assert shaped["matches"][0]["file_paths"] is None
    assert "file_paths" not in shaped["matches"][1]


def test_compact_tool_result_caps_known_large_lists() -> None:
    shaped = compact_tool_result(
        "qbt_torrent_detail",
        {"peers": [{"ip": str(i)} for i in range(100)]},
    )

    assert shaped["peer_count"] == 100
    assert len(shaped["peers"]) < 100
