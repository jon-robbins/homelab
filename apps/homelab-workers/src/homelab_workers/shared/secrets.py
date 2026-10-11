from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

WORKER_CONTAINER_SECRETS: tuple[str, ...] = (
    "QBITTORRENT_USERNAME",
    "QBITTORRENT_PASSWORD",
    "SONARR_API_KEY",
    "RADARR_API_KEY",
    "TMDB_API_KEY",
    "PROWLARR_API_KEY",
    "OVERSEERR_API_KEY",
)


def get_secret(name: str, default: str | None = None) -> str | None:
    explicit_file = os.getenv(f"{name}_FILE")
    path = Path(explicit_file or f"/run/secrets/{name}")
    if path.is_file():
        return path.read_text(encoding="utf-8").rstrip("\r\n")
    return os.getenv(name, default)


def hydrate_managed_secrets(names: Iterable[str]) -> None:
    """Load managed secrets into os.environ with file-first precedence.

    If a secret file (NAME_FILE or /run/secrets/NAME) exists, its contents
    always overwrite any existing environment value. Environment-only values
    are left unchanged when no file is present.
    """
    for name in names:
        explicit_file = os.getenv(f"{name}_FILE")
        path = Path(explicit_file or f"/run/secrets/{name}")
        if path.is_file():
            os.environ[name] = path.read_text(encoding="utf-8").rstrip("\r\n")


def bootstrap_container_secrets(
    names: Iterable[str] | None = None,
) -> None:
    hydrate_managed_secrets(names or WORKER_CONTAINER_SECRETS)
