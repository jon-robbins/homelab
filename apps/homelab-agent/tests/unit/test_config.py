from __future__ import annotations

from app.config import get_settings


def test_settings_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("HOMELAB_AGENT_API_TOKEN", "abc123")
    monkeypatch.setenv("HOMELAB_AGENT_MODEL_NAME", "Qwen3.8-27B")
    monkeypatch.setenv("HOMELAB_AGENT_MODEL_BASE_URL", "http://qwen.test/v1")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.api_token == "abc123"
    assert settings.model_name == "Qwen3.8-27B"
    assert settings.model_base_url == "http://qwen.test/v1"
