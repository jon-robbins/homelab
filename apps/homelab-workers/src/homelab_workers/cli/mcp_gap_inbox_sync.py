from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from homelab_workers.mcp_gap_closer.collect import discover_transcript_files, load_jsonl_messages
from homelab_workers.mcp_gap_closer.pack import build_inbox_pack
from homelab_workers.mcp_gap_closer.publish import publish_inbox_branch
from homelab_workers.mcp_gap_closer.redact import redact_text

DEFAULT_REPO_URL = "https://github.com/jon-robbins/homelab.git"


def _default_repo_url() -> str:
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


def _detect_runtime_root() -> Path | None:
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "docker-compose.yml").is_file() and (candidate / "data").is_dir():
            return candidate
    return None


def _runtime_data() -> Path:
    return Path(os.environ.get("APP_DATA_DIR", "data")).expanduser()


def _local_pack_path() -> Path | None:
    runtime_data = _runtime_data()
    if runtime_data.is_dir() or "APP_DATA_DIR" in os.environ:
        return runtime_data / "mcp-gap-inbox" / "latest.json"
    root = _detect_runtime_root()
    if root is not None:
        return root / "data" / "mcp-gap-inbox" / "latest.json"
    return None


def _collect_chats(projects_root: Path, *, now: float) -> list[dict]:
    chats: list[dict] = []
    for path in discover_transcript_files(projects_root, now=now):
        messages = load_jsonl_messages(path)
        if not messages:
            continue
        redacted = [{"role": m["role"], "text": redact_text(m["text"])} for m in messages]
        rel = path.relative_to(projects_root)
        project = rel.parts[0] if rel.parts else "unknown"
        chats.append({"id": path.stem, "project": project, "messages": redacted})
    return chats


def _write_local_pack(pack: dict, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync MCP gap inbox pack and publish branch")
    parser.add_argument(
        "--projects-root",
        type=Path,
        default=Path("~/.cursor/projects").expanduser(),
        help="Cursor projects root (default: ~/.cursor/projects)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print summary only; skip publish")
    parser.add_argument("--no-publish", action="store_true", help="Build pack locally; skip git push")
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=200_000,
        help="Maximum inbox pack size in bytes (default: 200000)",
    )
    parser.add_argument(
        "--repo-url",
        default=None,
        help="Git remote URL for publish clone (default: origin or fallback URL)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = time.time()
    generated_at = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    chats = _collect_chats(args.projects_root, now=now)
    pack = build_inbox_pack(chats=chats, generated_at=generated_at, max_bytes=args.max_bytes)

    local_path = _local_pack_path()
    if local_path is not None:
        _write_local_pack(pack, local_path)

    chat_count = len(pack.get("chats") or [])
    print(
        f"mcp-gap-inbox-sync: chats={chat_count} "
        f"bytes={len(json.dumps(pack, ensure_ascii=False).encode('utf-8'))}"
    )
    if args.dry_run:
        print("mcp-gap-inbox-sync: dry-run; publish skipped")
        return 0

    if not chat_count:
        print("mcp-gap-inbox-sync: empty pack; publish skipped")
        return 0

    if args.no_publish:
        print("mcp-gap-inbox-sync: --no-publish; publish skipped")
        return 0

    repo_url = args.repo_url or _default_repo_url()
    with tempfile.TemporaryDirectory(prefix="mcp-gap-inbox-") as tmp:
        publish_inbox_branch(
            repo_url=repo_url,
            pack=pack,
            work_dir=Path(tmp),
        )
    print("mcp-gap-inbox-sync: published chore/mcp-gap-inbox")
    return 0


if __name__ == "__main__":
    sys.exit(main())
