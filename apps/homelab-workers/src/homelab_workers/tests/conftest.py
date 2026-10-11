from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def sample_env_file(tmp_path: Path) -> Path:
    env = tmp_path / ".env"
    env.write_text(
        "PUID=1000\n"
        "PGID=1000\n"
        "TZ=America/New_York\n"
        "MEDIA_HDD_PATH=/mnt/media-hdd\n",
        encoding="utf-8",
    )
    return env


@pytest.fixture
def sonarr_config_xml(tmp_path: Path) -> Path:
    config = tmp_path / "config.xml"
    config.write_text(
        '<?xml version="1.0"?>\n'
        "<Config><ApiKey>test-api-key-123</ApiKey></Config>\n",
        encoding="utf-8",
    )
    return config
