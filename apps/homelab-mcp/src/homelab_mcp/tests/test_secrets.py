from __future__ import annotations

from pathlib import Path

from homelab_mcp.secrets import get_secret, hydrate_managed_secrets


def test_file_wins_over_env(tmp_path: Path, monkeypatch) -> None:
    secret_file = tmp_path / "TOKEN"
    secret_file.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv("TOKEN", "from-env")
    monkeypatch.setenv("TOKEN_FILE", str(secret_file))
    assert get_secret("TOKEN") == "from-file"


def test_name_file_wins_over_default_path(tmp_path: Path, monkeypatch) -> None:
    custom = tmp_path / "custom"
    custom.write_text("custom-path", encoding="utf-8")
    monkeypatch.setenv("TOKEN_FILE", str(custom))
    monkeypatch.delenv("TOKEN", raising=False)
    assert get_secret("TOKEN") == "custom-path"


def test_env_fallback_when_no_file(monkeypatch) -> None:
    monkeypatch.delenv("TOKEN_FILE", raising=False)
    monkeypatch.setenv("TOKEN", "from-env")
    assert get_secret("TOKEN") == "from-env"


def test_missing_returns_default(monkeypatch) -> None:
    monkeypatch.delenv("TOKEN", raising=False)
    monkeypatch.delenv("TOKEN_FILE", raising=False)
    assert get_secret("TOKEN") is None
    assert get_secret("TOKEN", default="fallback") == "fallback"


def test_file_rstrip_newlines_only_preserves_spaces(tmp_path: Path, monkeypatch) -> None:
    secret_file = tmp_path / "PASS"
    secret_file.write_text("  spaced secret  \n", encoding="utf-8")
    monkeypatch.setenv("PASS_FILE", str(secret_file))
    assert get_secret("PASS") == "  spaced secret  "


def test_hydrate_file_overwrites_existing_env(tmp_path: Path, monkeypatch) -> None:
    secret_file = tmp_path / "KEY"
    secret_file.write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("KEY", "existing")
    monkeypatch.setenv("KEY_FILE", str(secret_file))
    hydrate_managed_secrets(["KEY"])
    assert __import__("os").environ["KEY"] == "from-file"


def test_hydrate_mcp_api_token_file_override(tmp_path, monkeypatch):
    """MCP_API_TOKEN_FILE env forces reading server token from a dedicated path (for telegram-mcp isolation)."""
    dedicated = tmp_path / "telegram_mcp_token"
    dedicated.write_text("telegram-dedicated-token-123\n", encoding="utf-8")
    # Simulate primary MCP_API_TOKEN env var already set differently (should be OVERWRITTEN)
    monkeypatch.setenv("MCP_API_TOKEN", "should-be-overwritten")
    monkeypatch.setenv("MCP_API_TOKEN_FILE", str(dedicated))
    # Import secrets module fresh (already imported, hydrate honors env at call time)
    from homelab_mcp.secrets import hydrate_managed_secrets
    hydrate_managed_secrets(["MCP_API_TOKEN"])
    import os
    assert os.environ["MCP_API_TOKEN"] == "telegram-dedicated-token-123"
