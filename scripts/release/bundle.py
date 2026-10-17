"""Build and verify deterministic release bundle archives."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tarfile
from typing import Iterable


_REQUIRED_FILES = ("app.json", "edge.json", "site.yaml")
_FIXED_MTIME = 0
_FIXED_UID = 0
_FIXED_GID = 0


def _archive_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.mode = 0o644
    info.mtime = _FIXED_MTIME
    info.uid = _FIXED_UID
    info.gid = _FIXED_GID
    info.uname = ""
    info.gname = ""
    info.size = size
    return info


def _source_entries(root: Path) -> Iterable[tuple[str, Path]]:
    unexpected = sorted(path.name for path in root.iterdir() if path.name not in _REQUIRED_FILES)
    if unexpected:
        raise ValueError(f"unexpected bundle members: {', '.join(unexpected)}")
    for name in _REQUIRED_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"bundle member must be a regular file: {name}")
        yield name, path


def build(directory: str | Path) -> bytes:
    """Build an uncompressed, deterministic tar archive from *directory*."""
    root = Path(directory)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("bundle source must be a directory")
    output = BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, path in _source_entries(root):
            data = path.read_bytes()
            archive.addfile(_archive_info(name, len(data)), BytesIO(data))
    return output.getvalue()


def bundle_sha256(data: bytes) -> str:
    return "sha256:" + sha256(data).hexdigest()


def verify(data: bytes) -> None:
    """Validate that *data* contains exactly the v2 bundle shape."""
    try:
        with tarfile.open(fileobj=BytesIO(data), mode="r:") as archive:
            members = archive.getmembers()
    except (OSError, tarfile.TarError, TypeError) as error:
        raise ValueError("invalid tar archive") from error
    names: list[str] = []
    for member in members:
        if member.type != tarfile.REGTYPE or member.name not in _REQUIRED_FILES:
            raise ValueError(f"unexpected archive member: {member.name!r}")
        if member.name in names:
            raise ValueError(f"duplicate archive member: {member.name!r}")
        names.append(member.name)
    if names != list(_REQUIRED_FILES):
        raise ValueError("bundle members must be app.json, edge.json, site.yaml")
