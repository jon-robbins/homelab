"""Containment rules for media file tools: every path must live under a configured root."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

TRASH_DIR_NAME = ".homelab-trash"


class PathRejected(ValueError):
    """Raised when a requested path falls outside the configured media roots."""


@dataclass(frozen=True)
class ResolvedPath:
    path: Path
    root: Path

    @property
    def trash_dir(self) -> Path:
        return self.root / TRASH_DIR_NAME


def resolved_roots(roots: tuple[str, ...]) -> tuple[Path, ...]:
    return tuple(Path(os.path.realpath(root)) for root in roots)


def resolve_in_roots(
    raw: str, roots: tuple[str, ...], *, allow_root: bool = False
) -> ResolvedPath:
    """Resolve the parent strictly and keep the leaf name, so a symlink leaf is
    handled as a link rather than followed to its target. Roots themselves are only
    accepted for reads (``allow_root``); mutations can never target a whole root."""
    if not raw or not os.path.isabs(raw):
        raise PathRejected("path must be absolute")
    normalized = Path(os.path.normpath(raw))
    if normalized.name in ("", ".", ".."):
        raise PathRejected("path must name an entry inside a media root")
    path = Path(os.path.realpath(normalized.parent)) / normalized.name
    for root in resolved_roots(roots):
        if path == root:
            if allow_root:
                return ResolvedPath(path=path, root=root)
            raise PathRejected("media roots themselves cannot be targeted")
        if root not in path.parents:
            continue
        if path.relative_to(root).parts[0] == TRASH_DIR_NAME:
            raise PathRejected("trash entries are managed by media_trash_* tools")
        return ResolvedPath(path=path, root=root)
    raise PathRejected(f"path is outside the media roots: {', '.join(roots)}")
