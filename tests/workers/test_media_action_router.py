from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "media_action_router.py"
SPEC = importlib.util.spec_from_file_location("media_action_router", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

format_response = MODULE.format_response
run_router = MODULE.run_router


def test_format_response_options() -> None:
    action = {"action": "download_options_tv", "query": "Show", "season": 4}
    result = {
        "ok": True,
        "options": [
            {
                "title": "Show S04 pack",
                "seeders": 25,
                "leechers": 2,
                "size_human": "8.1 GB",
                "indexer": "IndexerOne",
            }
        ],
    }
    text = format_response(action, result)
    assert text.startswith("Got it! Here are some options")
    assert "Show S04 pack" in text
    assert "seeders 25" in text


def test_format_response_grab_success() -> None:
    action = {"action": "download_grab_tv", "guid": "abc", "episode_id": 10}
    result = {"ok": True, "command": {"name": "EpisodeSearch"}}
    text = format_response(action, result)
    assert text == "OK! It's downloading."


def test_run_router_pipeline(monkeypatch) -> None:
    expected_action = {
        "action": "download_options_tv",
        "query": "Crazy Ex-Girlfriend",
        "season": 4,
        "series_id": None,
        "include_full_series_packs": True,
    }
    expected_result = {
        "ok": True,
        "options": [
            {
                "title": "Crazy Ex-Girlfriend S04",
                "seeders": 11,
                "leechers": 1,
                "size_human": "3.2 GB",
                "indexer": "Unit",
            }
        ],
    }

    def _fake_parse(*_: object, **__: object) -> dict:
        return expected_action

    def _fake_exec(*_: object, **__: object) -> dict:
        return expected_result

    monkeypatch.setattr(MODULE, "parse_action", _fake_parse)
    monkeypatch.setattr(MODULE, "execute_action", _fake_exec)

    action, result, text = run_router(
        "Get Crazy Ex Girlfriend season 4.",
        model="dummy",
        ollama_url="http://example",
        media_agent_url="http://media-agent",
        media_agent_token="token",
    )
    assert action == expected_action
    assert result == expected_result
    assert text.startswith("Got it! Here are some options")
