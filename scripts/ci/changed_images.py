#!/usr/bin/env python3
"""Report whether application images changed in the current GitHub checkout."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


IMAGE_PATHS = {
    "homelab-agent": "apps/homelab-agent/",
    "homelab-mcp": "apps/homelab-mcp/",
    "homelab-workers": "apps/homelab-workers/",
    "media-agent": "apps/media-agent/",
    "qwen-code-telegram": "apps/qwen-code-telegram/",
    "qwen-serve": "apps/qwen-serve/",
    "caddy": "config/caddy/",
}


def _changed_paths() -> list[str]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    base = ""
    if event_path and Path(event_path).exists():
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        base = event.get("pull_request", {}).get("base", {}).get("sha", "")
    if not base:
        base = os.environ.get("GITHUB_BASE_SHA", "")

    if base:
        command = ["git", "diff", "--name-only", f"{base}...HEAD"]
    else:
        command = ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if line]


def changed_images() -> dict[str, bool]:
    paths = _changed_paths()
    return {
        image: any(path == prefix.rstrip("/") or path.startswith(prefix) for path in paths)
        for image, prefix in IMAGE_PATHS.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--image", choices=sorted(IMAGE_PATHS))
    args = parser.parse_args()
    result = changed_images()
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            for image, changed in result.items():
                output.write(f"{image}={'true' if changed else 'false'}\n")
    if args.image:
        print("true" if result[args.image] else "false")
    else:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
