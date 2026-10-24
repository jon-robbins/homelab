from __future__ import annotations

import json

from homelab_mcp.envelope import error_response, ok_response


def test_ok_response_shape() -> None:
    payload = ok_response("qbt_transfer_summary", {"connection_status": "connected"}, duration_ms=12)
    body = json.loads(payload)
    assert body["ok"] is True
    assert body["tool"] == "qbt_transfer_summary"
    assert body["data"]["connection_status"] == "connected"
    assert body["meta"]["duration_ms"] == 12
    assert body["meta"]["generated_at"].endswith("Z")


def test_error_response_shape() -> None:
    payload = error_response(
        "qbt_torrent_detail",
        code="NOT_FOUND",
        message="missing hash",
        hint="use qbt_torrents_list",
    )
    body = json.loads(payload)
    assert body["ok"] is False
    assert body["tool"] == "qbt_torrent_detail"
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "missing hash"
    assert body["error"]["hint"] == "use qbt_torrents_list"
    assert body["meta"]["generated_at"].endswith("Z")


def test_responses_redact_secret_values() -> None:
    ok = ok_response(
        "tool",
        {
            "token": "SECRET",
            "url": "https://example.invalid/path?apikey=SECRET&ok=1",
            "nested": {"Authorization": "Bearer SECRET"},
        },
        duration_ms=1,
    )
    err = error_response(
        "tool",
        code="BAD",
        message="failed with password=SECRET",
        hint="Use Authorization: Bearer SECRET",
    )

    assert "SECRET" not in ok
    assert "SECRET" not in err
    assert "apikey=[redacted]" in ok
    assert "password=[redacted]" in err


def test_ok_response_includes_evidence_record() -> None:
    payload = ok_response("tool", {"value": 1}, duration_ms=1)
    body = json.loads(payload)
    assert body["evidence"][0]["source"] == "tool"
    assert body["evidence"][0]["status"] == "confirmed"


def test_ok_response_evidence_does_not_duplicate_shaped_payload() -> None:
    large_value = "payload-value-" * 100
    payload = ok_response(
        "tool",
        {"title": "Example", "large_value": large_value, "nested": {"value": 1}},
        duration_ms=1,
    )
    body = json.loads(payload)

    assert body["data"]["large_value"] == large_value
    facts = body["evidence"][0]["facts"]
    assert large_value not in json.dumps(facts)
    assert facts == {
        "field_count": 3,
        "fields": ["large_value", "nested", "title"],
    }
