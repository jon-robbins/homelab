from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.router.smoke import smoke_gate_verify_season_only

AUTH = {"Authorization": "Bearer test-bearer-secret"}


def test_router_smoke_gate_endpoint_contract() -> None:
    with TestClient(app) as client:
        r = client.get(
            "/internal/media-agent/v1/router-smoke-gate?session_key=test-smoke",
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["scenario"] == "cxg-season-4-first-option"
    assert d["session_key"] == "test-smoke"
    assert len(d["steps"]) == 2
    assert d["steps"][1]["message"] == "first option"
    assert d["verify"]["helper"] == "smoke_gate_verify_season_only(tool_result, season=4)"


def test_smoke_gate_verify_helper() -> None:
    assert (
        smoke_gate_verify_season_only(
            {
                "season_selection": {
                    "status": "season_only_applied",
                    "season": 4,
                    "enabled_file_count": 10,
                }
            },
            season=4,
        )
        is True
    )
    assert (
        smoke_gate_verify_season_only(
            {
                "season_selection": {
                    "status": "season_only_applied",
                    "season": 5,
                    "enabled_file_count": 10,
                }
            },
            season=4,
        )
        is False
    )
