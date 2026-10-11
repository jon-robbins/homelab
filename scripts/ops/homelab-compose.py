#!/usr/bin/env python3
"""DEPRECATED / COMPATIBILITY WRAPPER: Secret-aware docker compose wrapper.

This wrapper fetches Infisical secrets and injects them into environment variables
for legacy/compatibility use. The canonical secret delivery mechanism is file-backed
delivery via Infisical Agent (HOMELAB_SECRET_DIR). Kept callable for CI and backwards
compatibility during rollout.

Usage: homelab-compose.py [docker compose args...]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent  # <repo>/scripts/ops
REPO_ROOT = SCRIPT_DIR.parent.parent  # <repo> — parents[1]; .parent alone is scripts/
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from infisical_fetch import fetch_secrets, split_csv
from managed_secrets import managed_secret_keys_for_paths

COMPOSE_PATHS = "/network,/media,/arr,/qbt,/llm"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("usage: homelab-compose.py [docker compose args...]", file=sys.stderr)
        return 1

    if args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    required_keys = managed_secret_keys_for_paths(split_csv(COMPOSE_PATHS))
    env_vars = fetch_secrets(COMPOSE_PATHS, required_keys=required_keys)
    os.environ.update(env_vars)
    cmd = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "--project-directory",
        str(REPO_ROOT),
        *args,
    ]
    return os.execvp("docker", cmd)


if __name__ == "__main__":
    sys.exit(main())
