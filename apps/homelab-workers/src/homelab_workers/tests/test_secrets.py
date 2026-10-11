from __future__ import annotations

from pathlib import Path

from homelab_workers.shared.secrets import (
    WORKER_CONTAINER_SECRETS,
    bootstrap_container_secrets,
    get_secret,
    hydrate_managed_secrets,
)


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


def test_hydrate_leaves_env_when_no_file(monkeypatch) -> None:
    monkeypatch.delenv("KEY_FILE", raising=False)
    monkeypatch.setenv("KEY", "existing")
    hydrate_managed_secrets(["KEY"])
    assert __import__("os").environ["KEY"] == "existing"


def test_bootstrap_container_secrets_hydrates_from_file(
    tmp_path: Path, monkeypatch
) -> None:
    qbt_pass = tmp_path / "QBITTORRENT_PASSWORD"
    qbt_pass.write_text("secret-pass\n", encoding="utf-8")
    monkeypatch.setenv("QBITTORRENT_PASSWORD_FILE", str(qbt_pass))
    monkeypatch.delenv("QBITTORRENT_PASSWORD", raising=False)
    bootstrap_container_secrets(["QBITTORRENT_PASSWORD"])
    assert __import__("os").environ["QBITTORRENT_PASSWORD"] == "secret-pass"


def test_worker_container_secrets_include_seerr_reconciler_keys() -> None:
    assert "OVERSEERR_API_KEY" in WORKER_CONTAINER_SECRETS
    assert "PROWLARR_API_KEY" in WORKER_CONTAINER_SECRETS


def test_arr_post_import_hydrates_when_env_stripped(tmp_path: Path, monkeypatch) -> None:
    """docker exec into MCP has /run/secrets files but no Compose environment secrets."""
    import os

    from homelab_workers.cli.qbt_arr_post_import import _ARR_POST_IMPORT_SECRETS

    for name in _ARR_POST_IMPORT_SECRETS:
        path = tmp_path / name
        path.write_text(f"{name}-from-file\n", encoding="utf-8")
        monkeypatch.setenv(f"{name}_FILE", str(path))
        monkeypatch.delenv(name, raising=False)

    bootstrap_container_secrets(_ARR_POST_IMPORT_SECRETS)
    for name in _ARR_POST_IMPORT_SECRETS:
        assert os.environ[name] == f"{name}-from-file"
