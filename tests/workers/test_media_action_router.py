from __future__ import annotations

import importlib.util
import os
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "media_action_router.py"
SPEC = importlib.util.spec_from_file_location("media_action_router", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

format_response = MODULE.format_response
run_router = MODULE.run_router


def _load_dotenv() -> dict[str, str]:
    """Read .env at repo root into a dict (simple KEY=VALUE parser)."""
    env_file = REPO_ROOT / ".env"
    vals: dict[str, str] = {}
    if not env_file.exists():
        return vals
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        vals[key.strip()] = value.strip()
    return vals


_DOTENV = _load_dotenv()


def _env(key: str, default: str = "") -> str:
    """Get a value from os.environ first, then .env file, then default."""
    return os.environ.get(key) or _DOTENV.get(key) or default


_GEMINI_API_KEY = _env("GEMINI_API_KEY")
_MEDIA_AGENT_TOKEN = _env("MEDIA_AGENT_TOKEN")
_MEDIA_AGENT_URL = _env("MEDIA_AGENT_URL", "http://127.0.0.1:8000")


def _media_agent_reachable() -> bool:
    """Quick connectivity check to media-agent."""
    try:
        req = urllib.request.Request(
            f"{_MEDIA_AGENT_URL.rstrip('/')}/docs", method="GET",
        )
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception:  # noqa: BLE001
        return False


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


@pytest.mark.skipif(not _GEMINI_API_KEY, reason="GEMINI_API_KEY not set")
@pytest.mark.skipif(not _MEDIA_AGENT_TOKEN, reason="MEDIA_AGENT_TOKEN not set")
def test_run_router_pipeline() -> None:
    """Full end-to-end pipeline: real Gemini parse + real execute_action."""
    if not _media_agent_reachable():
        pytest.skip(f"media-agent not reachable at {_MEDIA_AGENT_URL}")

    action, result, text = run_router(
        "Get Crazy Ex Girlfriend season 4.",
        provider="gemini",
        model="gemini-2.5-flash-lite",
        gemini_api_key=_GEMINI_API_KEY,
        media_agent_url=_MEDIA_AGENT_URL,
        media_agent_token=_MEDIA_AGENT_TOKEN,
    )
    assert isinstance(action, dict)
    assert "action" in action
    assert action["action"] in {
        "search",
        "download_options_tv",
        "download_options_movie",
        "download_grab_tv",
        "download_grab_movie",
        "indexer_search",
        "indexer_grab",
    }
    assert isinstance(result, dict)
    assert isinstance(text, str) and len(text) > 0
