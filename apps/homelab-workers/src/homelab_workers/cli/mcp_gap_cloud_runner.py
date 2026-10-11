from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from homelab_workers.mcp_gap_closer.runner import RunnerConfig, run_closer

DEFAULT_REPO_URL = "https://github.com/jon-robbins/homelab.git"


def _default_repo_url() -> str:
    env_url = os.environ.get("MCP_GAP_REPO_URL", "").strip()
    if env_url:
        return env_url
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
        url = result.stdout.strip()
        if url:
            return url
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return DEFAULT_REPO_URL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch MCP gap closer Cloud Agent and babysit CI")
    parser.add_argument("--dry-run", action="store_true", help="Describe launch without calling SDK")
    parser.add_argument("--skip-merge", action="store_true", help="Do not auto-merge green PRs")
    parser.add_argument("--repo-url", default=None, help="Repository URL for cloud clone")
    parser.add_argument("--model", default="composer-2.5", help="Cursor model id")
    parser.add_argument("--poll-seconds", type=int, default=60, help="CI poll interval")
    parser.add_argument("--gh-bin", default="gh", help="GitHub CLI binary")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key and not args.dry_run:
        print("mcp-gap-cloud-runner: CURSOR_API_KEY is required", file=sys.stderr)
        return 1

    config = RunnerConfig(
        api_key=api_key or "dry-run",
        repo_url=args.repo_url or _default_repo_url(),
        model=args.model,
        dry_run=args.dry_run,
        skip_merge=args.skip_merge,
        gh_bin=args.gh_bin,
        poll_seconds=args.poll_seconds,
    )
    result = run_closer(config)
    payload = {
        "status": result.status,
        "pr_url": result.pr_url,
        "agent_id": result.agent_id,
        "detail": result.detail,
    }
    print(json.dumps(payload, indent=2))
    if result.status in {"error", "hard_stopped"}:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
