from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

MCP_MANAGED_SECRETS: tuple[str, ...] = (
    "MCP_API_TOKEN",
    "MEDIA_AGENT_TOKEN",
    "QBITTORRENT_USERNAME",
    "QBITTORRENT_PASSWORD",
    "SONARR_API_KEY",
    "RADARR_API_KEY",
    "BAZARR_API_KEY",
    "PROWLARR_API_KEY",
    "OVERSEERR_API_KEY",
    "AUTOBRR_API_KEY",
    "PLEX_TOKEN",
)


def get_secret(name: str, default: str | None = None) -> str | None:
    explicit_file = os.getenv(f"{name}_FILE")
    path = Path(explicit_file or f"/run/secrets/{name}")
    if path.is_file():
        return path.read_text(encoding="utf-8").rstrip("\r\n")
    return os.getenv(name, default)


def hydrate_managed_secrets(names: Iterable[str]) -> None:
    """File-backed secrets always win over pre-existing environment values."""
    for name in names:
        explicit_file = os.getenv(f"{name}_FILE")
        path = Path(explicit_file or f"/run/secrets/{name}")
        if path.is_file():
            os.environ[name] = path.read_text(encoding="utf-8").rstrip("\r\n")

