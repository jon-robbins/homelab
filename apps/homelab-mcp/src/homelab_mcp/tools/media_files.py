"""Read-only media file tools: directory listing and trash inspection."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homelab_mcp.config import Settings
from homelab_mcp.envelope import error_response, ok_response
from homelab_mcp.file_paths import TRASH_DIR_NAME, PathRejected, resolve_in_roots, resolved_roots

TOOL_FILES_LIST = "media_files_list"
TOOL_TRASH_LIST = "media_trash_list"
MAX_ENTRIES = 500


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")


def tree_size(path: Path) -> int:
    if path.is_symlink() or not path.is_dir():
        return path.lstat().st_size
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            total += (Path(dirpath) / name).lstat().st_size
    return total


def describe(path: Path, *, name: str | None = None, deep_size: bool = False) -> dict[str, Any]:
    stat = path.lstat()
    if path.is_symlink():
        kind = "symlink"
    elif path.is_dir():
        kind = "dir"
    else:
        kind = "file"
    size = tree_size(path) if deep_size else (stat.st_size if kind == "file" else None)
    return {"name": name or path.name, "type": kind, "size_bytes": size, "mtime": _iso(stat.st_mtime)}


def files_list(settings: Settings, *, path: str | None, limit: int = 200) -> str:
    started = time.perf_counter()
    tool = TOOL_FILES_LIST
    if path is None:
        entries = [describe(root, name=str(root)) for root in resolved_roots(settings.media_file_roots) if root.is_dir()]
        return ok_response(tool, {"path": None, "entries": entries}, duration_ms=_ms(started))
    try:
        target = resolve_in_roots(path, settings.media_file_roots, allow_root=True).path
    except PathRejected as exc:
        return error_response(tool, code="PATH_REJECTED", message=str(exc))
    if not target.is_dir():
        if target.exists():
            return ok_response(tool, {"path": str(target), "entries": [describe(target)]}, duration_ms=_ms(started))
        return error_response(tool, code="NOT_FOUND", message=f"{target} does not exist")
    children = sorted(target.iterdir(), key=lambda p: p.name)
    capped = max(1, min(limit, MAX_ENTRIES))
    entries = [describe(child) for child in children[:capped] if child.name != TRASH_DIR_NAME]
    data = {"path": str(target), "entries": entries, "truncated": len(children) > capped}
    return ok_response(tool, data, duration_ms=_ms(started))


def trash_entries(settings: Settings) -> list[tuple[Path, dict[str, Any]]]:
    rows = []
    for root in resolved_roots(settings.media_file_roots):
        trash = root / TRASH_DIR_NAME
        if not trash.is_dir():
            continue
        for child in sorted(trash.iterdir(), key=lambda p: p.name):
            rows.append((child, {**describe(child, deep_size=True), "root": str(root)}))
    return rows


def trash_list(settings: Settings) -> str:
    started = time.perf_counter()
    entries = [row for _path, row in trash_entries(settings)]
    total = sum(row["size_bytes"] or 0 for row in entries)
    return ok_response(
        TOOL_TRASH_LIST,
        {"entries": entries, "count": len(entries), "total_bytes": total},
        duration_ms=_ms(started),
    )


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
