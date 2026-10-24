"""Tier-1 media file mutations: move, soft-delete into per-root trash, and trash purge."""

from __future__ import annotations

import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homelab_mcp.config import Settings
from homelab_mcp.file_paths import resolve_in_roots
from homelab_mcp.mutation_gate import (
    canonical_file_delete,
    canonical_file_move,
    canonical_trash_empty,
)
from homelab_mcp.mutation_runner import Refused, run_mutation
from homelab_mcp.tools.media_files import trash_entries

TOOL_FILE_MOVE = "media_file_move"
TOOL_FILE_DELETE = "media_file_delete"
TOOL_TRASH_EMPTY = "media_trash_empty"


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def file_move(
    settings: Settings, *, src: str, dest: str, expected_action: str | None = None
) -> str:
    def paths() -> tuple[Path, Path]:
        return (
            resolve_in_roots(src, settings.media_file_roots).path,
            resolve_in_roots(dest, settings.media_file_roots).path,
        )

    def apply() -> dict[str, Any]:
        source, target = paths()
        if not _exists(source):
            raise Refused("NOT_FOUND", f"{source} does not exist")
        if _exists(target):
            raise Refused("DESTINATION_EXISTS", f"{target} already exists; refusing to overwrite")
        if source in target.parents:
            raise Refused("INVALID_ARGUMENT", "cannot move a directory inside itself")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        return {"src": str(source), "dest": str(target)}

    return run_mutation(
        settings,
        tool=TOOL_FILE_MOVE,
        tier=1,
        gate=lambda: canonical_file_move(*map(str, paths())),
        provided=expected_action,
        apply=apply,
    )


def file_delete(settings: Settings, *, path: str, expected_action: str | None = None) -> str:
    def apply() -> dict[str, Any]:
        resolved = resolve_in_roots(path, settings.media_file_roots)
        source = resolved.path
        if not _exists(source):
            raise Refused("NOT_FOUND", f"{source} does not exist")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        resolved.trash_dir.mkdir(exist_ok=True)
        trash_path = resolved.trash_dir / f"{stamp}__{source.name}"
        source.rename(trash_path)
        return {"path": str(source), "trash_path": str(trash_path)}

    return run_mutation(
        settings,
        tool=TOOL_FILE_DELETE,
        tier=1,
        gate=lambda: canonical_file_delete(str(resolve_in_roots(path, settings.media_file_roots).path)),
        provided=expected_action,
        apply=apply,
    )


def trash_empty(
    settings: Settings, *, older_than_days: int = 7, expected_action: str | None = None
) -> str:
    def apply() -> dict[str, Any]:
        if older_than_days < 0:
            raise Refused("INVALID_ARGUMENT", "older_than_days must be >= 0")
        cutoff = time.time() - older_than_days * 86400
        removed, freed = [], 0
        for entry, row in trash_entries(settings):
            if entry.lstat().st_mtime > cutoff:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed.append(str(entry))
            freed += row["size_bytes"] or 0
        return {"removed_count": len(removed), "freed_bytes": freed, "removed": removed}

    return run_mutation(
        settings,
        tool=TOOL_TRASH_EMPTY,
        tier=1,
        gate=lambda: canonical_trash_empty(older_than_days),
        provided=expected_action,
        apply=apply,
    )
