import json
from types import SimpleNamespace

from homelab_mcp.tools import media


def test_seerr_request_diagnosis_reads_state_and_status(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    status_path = tmp_path / "status.json"
    state_path.write_text(
        json.dumps(
            {
                "requests": {
                    "77:tv:1": {
                        "request_id": 77,
                        "diagnosed_at": 100_000,
                        "title": "Stag",
                        "state": "blocked_no_candidate",
                        "reason": "no candidate",
                    },
                    "88:tv:1": {
                        "request_id": 88,
                        "diagnosed_at": 100_000,
                        "title": "Other",
                        "state": "downloaded",
                    },
                    "99:tv:1": {
                        "request_id": 99,
                        "diagnosed_at": 0,
                        "title": "Stale Stag",
                        "state": "downloaded",
                    },
                    "100:tv:1": {
                        "request_id": 100,
                        "title": "Legacy Stag",
                        "state": "downloaded",
                    },
                }
            }
        )
    )
    status_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "fallback_enabled": False,
                "running": False,
                "ok": True,
                "dry_run": True,
                "last_started_at": 100_000,
                "last_completed_at": 100_000,
                "diagnosed": 2,
                "mutated": 0,
                "blocked": 1,
                "last_error": None,
            }
        )
    )
    settings = SimpleNamespace(
        seerr_reconciler_state_path=str(state_path),
        seerr_reconciler_status_path=str(status_path),
        seerr_reconciler_status_max_age_seconds=900,
    )
    monkeypatch.setattr(media.time, "time", lambda: 100_100)

    payload = json.loads(media.seerr_request_diagnosis(settings, request_id=77))

    assert payload["ok"] is True
    assert payload["data"]["requests"][0]["request_id"] == 77
    assert payload["data"]["requests"][0]["reason"] == "no candidate"
    assert payload["data"]["reconciler"]["health"]["healthy"] is True

    title_payload = json.loads(media.seerr_request_diagnosis(settings, title="stag"))
    assert [row["request_id"] for row in title_payload["data"]["requests"]] == [77]
