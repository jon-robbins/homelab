from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | str) -> dict[str, str]:
    """Load a .env file, handling optional 'export KEY=value' lines."""
    env: dict[str, str] = {}
    source = Path(path)
    if not source.exists() or not source.is_file():
        return env

    for raw_line in source.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        env[key] = value.strip().strip('"').strip("'")
    return env


def load_dotenv_into_environ(path: Path | str) -> None:
    """Load key/value pairs into os.environ without overriding existing keys."""
    for key, value in load_dotenv(path).items():
        os.environ.setdefault(key, value)
