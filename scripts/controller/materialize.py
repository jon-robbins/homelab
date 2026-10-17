"""Atomic unpacking of verified release bundles."""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
import tarfile
import tempfile
from typing import Any


def materialize(candidate: Any, releases: str | Path) -> Path:
    """Unpack a candidate into ``releases/<sequence>`` and make it read-only."""
    root = Path(releases)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / str(candidate.sequence)
    if destination.exists():
        return destination
    with tempfile.TemporaryDirectory(prefix=f".{candidate.sequence}-", dir=root) as directory:
        temporary = Path(directory)
        with tarfile.open(fileobj=BytesIO(candidate.bundle_bytes), mode="r:") as archive:
            archive.extractall(temporary)
        for name in ("app.json", "edge.json", "site.yaml"):
            if (temporary / "files" / name).is_file():
                (temporary / name).symlink_to(Path("files") / name)
        for path in sorted(temporary.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        os.chmod(temporary, 0o555)
        os.rename(temporary, destination)
    return destination
