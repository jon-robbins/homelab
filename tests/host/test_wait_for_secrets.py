from __future__ import annotations

import os
from pathlib import Path
import subprocess


SCRIPT = Path(__file__).parents[2] / "host/wait-for-secrets"


def run(tmp_path: Path, rendered: dict[str, str], *, config: bool = True) -> subprocess.CompletedProcess[str]:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for name, value in rendered.items():
        (secrets / name).write_text(value)
    agent_config = tmp_path / "config.yaml"
    if config:
        agent_config.write_text(
            "templates:\n"
            f"  - destination-path: {secrets}/TMDB_API_KEY\n"
            f"  - destination-path: {secrets}/CLOUDFLARE_TOKEN\n"
        )
    env = {**os.environ, "CONFIG": str(agent_config), "SECRET_DIR": str(secrets), "TIMEOUT": "1"}
    return subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10)


def test_returns_once_every_rendered_secret_exists(tmp_path: Path):
    result = run(tmp_path, {"TMDB_API_KEY": "a", "CLOUDFLARE_TOKEN": "b"})
    assert result.returncode == 0 and result.stderr == ""


def test_names_missing_and_empty_secrets_but_never_fails_the_agent(tmp_path: Path):
    result = run(tmp_path, {"TMDB_API_KEY": ""})
    assert result.returncode == 0
    assert "secrets not rendered after 1s: CLOUDFLARE_TOKEN TMDB_API_KEY" in result.stderr


def test_config_without_tmpfs_destinations_does_not_wait(tmp_path: Path):
    result = run(tmp_path, {}, config=False)
    assert result.returncode == 0 and result.stderr == ""
