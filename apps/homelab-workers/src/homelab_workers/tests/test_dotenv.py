from __future__ import annotations

from pathlib import Path

from homelab_workers.shared.dotenv import load_dotenv, load_dotenv_into_environ


def test_load_dotenv_parses_basic_export_comments_and_quotes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n"
        "# comment\n"
        "FOO=bar\n"
        "export BAZ='qux'\n"
        'QUOTED="quoted value"\n'
        "INVALID_LINE\n",
        encoding="utf-8",
    )

    parsed = load_dotenv(env_file)

    assert parsed == {"FOO": "bar", "BAZ": "qux", "QUOTED": "quoted value"}


def test_load_dotenv_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_dotenv(tmp_path / "missing.env") == {}


def test_load_dotenv_into_environ_sets_missing_without_overriding(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP=from_file\nNEW=value\n", encoding="utf-8")
    monkeypatch.setenv("KEEP", "existing")

    load_dotenv_into_environ(env_file)

    assert "KEEP" in load_dotenv(env_file)
    assert "NEW" in load_dotenv(env_file)
    assert __import__("os").environ["KEEP"] == "existing"
    assert __import__("os").environ["NEW"] == "value"
