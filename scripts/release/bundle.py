"""Build and verify deterministic release bundle archives."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tarfile
from typing import Iterable


_REQUIRED_FILES = ("applications.json", "compose.yaml", "probe-targets.json")
_V2_REQUIRED_FILES = ("app.json", "edge.json", "site.yaml")
_OPTIONAL_FILES = "gpu-inputs.json"
_FIXED_MTIME = 0
_FIXED_UID = 0
_FIXED_GID = 0


def _archive_info(name: str, *, directory: bool, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    info.mode = 0o755 if directory else 0o644
    info.mtime = _FIXED_MTIME
    info.uid = _FIXED_UID
    info.gid = _FIXED_GID
    info.uname = ""
    info.gname = ""
    info.size = size
    return info


def _source_entries(root: Path) -> Iterable[tuple[str, Path, bool]]:
    required = _V2_REQUIRED_FILES if all((root / name).is_file() for name in _V2_REQUIRED_FILES) else _REQUIRED_FILES
    for name in required + ((_OPTIONAL_FILES,) if required == _REQUIRED_FILES else ()):
        path = root / name
        if name == _OPTIONAL_FILES and not path.exists() and not path.is_symlink():
            continue
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"bundle member must be a regular file: {name}")
        yield name, path, False

    files = root / "files"
    if required == _REQUIRED_FILES:
        if not files.is_dir() or files.is_symlink():
            raise ValueError("bundle requires a regular files directory")
        yield "files/", files, True
        for path in files.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError(f"bundle member must be a regular file or directory: {relative}")
            yield relative + ("/" if path.is_dir() else ""), path, path.is_dir()


def build(directory: str | Path) -> bytes:
    """Build an uncompressed, deterministic tar archive from *directory*."""
    root = Path(directory)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("bundle source must be a directory")

    entries = sorted(_source_entries(root), key=lambda entry: entry[0])
    output = BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, path, directory_entry in entries:
            if directory_entry:
                archive.addfile(_archive_info(name, directory=True))
            else:
                data = path.read_bytes()
                archive.addfile(_archive_info(name, directory=False, size=len(data)), BytesIO(data))
    return output.getvalue()


def bundle_sha256(data: bytes) -> str:
    """Return the release-record digest for bundle bytes."""
    return "sha256:" + sha256(data).hexdigest()


def _validate_member_name(name: str) -> list[str]:
    if not name or name.startswith("/"):
        raise ValueError(f"invalid archive path: {name!r}")
    components = name.split("/")
    if any(component in ("", ".", "..") for component in components[:-1]):
        raise ValueError(f"invalid archive path: {name!r}")
    if components[-1] == ".." or components[-1] == ".":
        raise ValueError(f"invalid archive path: {name!r}")
    return components


def verify(data: bytes) -> None:
    """Validate that *data* is an archive with the bundle shape."""
    try:
        with tarfile.open(fileobj=BytesIO(data), mode="r:") as archive:
            members = archive.getmembers()
    except (OSError, tarfile.TarError, TypeError) as error:
        raise ValueError("invalid tar archive") from error

    names: set[str] = set()
    raw_names: set[str] = set()
    directories: set[str] = set()
    for member in members:
        raw_name = member.name.rstrip("/")
        if raw_name in raw_names:
            raise ValueError(f"duplicate archive member: {raw_name!r}")
        raw_names.add(raw_name)
        if member.type == tarfile.DIRTYPE:
            name = member.name.rstrip("/") + "/"
            if name == "/":
                raise ValueError("invalid archive directory name")
        elif member.type == tarfile.REGTYPE:
            name = member.name
            if name.endswith("/"):
                raise ValueError("regular archive member cannot end with '/'")
        else:
            raise ValueError(f"unsupported archive member type: {member.name!r}")
        if name in names:
            raise ValueError(f"duplicate archive member: {name!r}")
        names.add(name)
        components = _validate_member_name(name)
        if member.type == tarfile.DIRTYPE:
            directories.add(name)

        if name != "files/" and not (
            name in _REQUIRED_FILES + _V2_REQUIRED_FILES
            or name == _OPTIONAL_FILES
            or name.startswith("files/")
        ):
            raise ValueError(f"unexpected archive member: {name!r}")
        if name.startswith("files/") and name != "files/":
            parent = "/".join(components[:-1]) + "/"
            if parent not in directories and parent != "files/":
                raise ValueError(f"missing parent directory: {parent!r}")

    required = _V2_REQUIRED_FILES if all(name in names for name in _V2_REQUIRED_FILES) else _REQUIRED_FILES
    if required == _REQUIRED_FILES and "files/" not in directories:
        raise ValueError("bundle requires files/")
    for name in required:
        if name not in names:
            raise ValueError(f"missing required archive member: {name!r}")
    if any(name in directories for name in required + ((_OPTIONAL_FILES,) if required == _REQUIRED_FILES else ())):
        raise ValueError("top-level bundle files must be regular files")
