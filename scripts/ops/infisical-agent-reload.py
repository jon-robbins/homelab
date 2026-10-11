#!/usr/bin/env python3
"""Infisical Agent change hook for scoped Compose consumer recreation."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence


DEFAULT_COMPOSE_FILE = Path(os.environ.get("HOMELAB_ROOT", Path.home() / "homelab")) / "docker-compose.yml"
DEFAULT_SECRET_DIR = Path(os.environ.get("HOMELAB_SECRET_DIR", Path.home() / ".config/homelab/secrets"))
DEFAULT_LOCK_PATH = Path.home() / ".cache/homelab/infisical-agent-reload.lock"


def get_secret_file(secret_dir: Path, key: str) -> Path:
    return secret_dir / key


def validate_secret_file(secret_file: Path, key: str) -> bool:
    if not secret_file.is_file():
        print(f"ERROR: secret file does not exist: {secret_file}", file=sys.stderr)
        return False
    try:
        size = secret_file.stat().st_size
    except OSError as e:
        print(f"ERROR: failed to stat secret file {secret_file}: {e}", file=sys.stderr)
        return False
    if size == 0:
        print(f"ERROR: secret file is empty for key {key}: {secret_file}", file=sys.stderr)
        return False
    return True


def discover_consumers(compose_config: dict, key: str) -> List[str]:
    services = compose_config.get("services") or {}
    consumers: List[str] = []
    for svc_name, svc_cfg in services.items():
        secrets = svc_cfg.get("secrets") or []
        for item in secrets:
            if isinstance(item, str):
                if item == key:
                    consumers.append(svc_name)
                    break
            elif isinstance(item, dict):
                src = item.get("source")
                tgt = item.get("target", "").split("/")[-1]
                if src == key or tgt == key:
                    consumers.append(svc_name)
                    break
    return sorted(consumers)


def reload_key(
    key: str,
    secret_dir: Optional[Path] = None,
    compose_file: Optional[Path] = None,
    lock_path: Optional[Path] = None,
) -> int:
    if secret_dir is None:
        env_dir = os.environ.get("HOMELAB_SECRET_DIR")
        secret_dir = Path(env_dir) if env_dir else DEFAULT_SECRET_DIR

    if compose_file is None:
        compose_file = DEFAULT_COMPOSE_FILE

    if lock_path is None:
        env_lock = os.environ.get("HOMELAB_INFISICAL_AGENT_LOCK")
        lock_path = Path(env_lock) if env_lock else DEFAULT_LOCK_PATH

    secret_file = get_secret_file(secret_dir, key)
    if not validate_secret_file(secret_file, key):
        return 1

    # Acquire lock for serialization
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = os.open(
            str(lock_path),
            os.O_CREAT | os.O_RDWR,
            0o600,
        )
    except OSError as e:
        print(f"ERROR: failed to open lock file {lock_path}: {e}", file=sys.stderr)
        return 1

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            cwd = compose_file.parent if compose_file.exists() else Path.cwd()
            cmd = [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "--profile",
                "*",
                "config",
                "--format",
                "json",
            ]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(cwd),
            )
            if res.returncode != 0:
                print(
                    f"ERROR: docker compose config failed (exit {res.returncode}): {res.stderr.strip()}",
                    file=sys.stderr,
                )
                return 1

            try:
                compose_config = json.loads(res.stdout)
            except json.JSONDecodeError as e:
                print(f"ERROR: failed to parse compose config JSON: {e}", file=sys.stderr)
                return 1

            consumers = discover_consumers(compose_config, key)
            if not consumers:
                print(f"infisical-agent-reload: no compose consumers found for secret {key}")
                return 0

            up_cmd = [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "up",
                "-d",
                "--no-deps",
                "--force-recreate",
                *consumers,
            ]
            up_res = subprocess.run(
                up_cmd,
                capture_output=True,
                text=True,
                cwd=str(cwd),
            )
            if up_res.returncode != 0:
                print(
                    f"ERROR: failed to recreate consumers {consumers} for secret {key}: {up_res.stderr.strip()}",
                    file=sys.stderr,
                )
                return 1

            print(
                f"infisical-agent-reload: successfully recreated {', '.join(consumers)} for secret {key}"
            )
            return 0
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Infisical Agent change hook for scoped Compose consumer reload."
    )
    parser.add_argument("--key", required=True, help="Canonical secret key name")
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=None,
        help="Path to docker-compose.yml (default: $HOMELAB_ROOT/docker-compose.yml)",
    )
    parser.add_argument(
        "--secret-dir",
        type=Path,
        default=None,
        help="Path to secrets directory (default: $HOME/.config/homelab/secrets)",
    )
    args = parser.parse_args(argv)
    return reload_key(
        key=args.key,
        secret_dir=args.secret_dir,
        compose_file=args.compose_file,
    )


if __name__ == "__main__":
    sys.exit(main())
