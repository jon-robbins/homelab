"""Smoke gate payload for the router's TV-season selection flow.

The OpenClaw frontend exercises this scenario regularly to verify the
season-only filter is wired correctly end-to-end.
"""

from __future__ import annotations

from typing import Any


def build_smoke_gate_payload(session_key: str = "smoke-cxg-s4") -> dict[str, Any]:
    return {
        "scenario": "cxg-season-4-first-option",
        "session_key": session_key,
        "steps": [
            {"message": "Get Crazy Ex-Girlfriend season 4", "session_key": session_key},
            {"message": "first option", "session_key": session_key},
        ],
        "expectations": {
            "follow_up_selection": {"rank": 1},
            "season_only_status": "season_only_applied",
        },
    }


def smoke_gate_verify_season_only(tool_result: dict[str, Any], season: int = 4) -> bool:
    season_selection = tool_result.get("season_selection")
    if not isinstance(season_selection, dict):
        return False
    return (
        season_selection.get("status") == "season_only_applied"
        and season_selection.get("season") == season
        and isinstance(season_selection.get("enabled_file_count"), int)
    )


__all__ = ["build_smoke_gate_payload", "smoke_gate_verify_season_only"]
