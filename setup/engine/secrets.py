"""Secret files in HOMELAB_SECRET_DIR, and handles that stand in for their values (0010)."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
import tempfile

_NAME = re.compile(r"[a-zA-Z\d_][a-zA-Z\d_.-]{0,99}", re.ASCII)
_MIN_REDACTED = 8  # shorter values would mangle ordinary text


@dataclass(frozen=True)
class SecretHandle:
    """A name, never a value. Steps pass these around; only `SecretStore.read` turns one into text."""

    name: str

    def __repr__(self) -> str:
        return f"SecretHandle({self.name})"

    __str__ = __repr__


class SecretStore:
    """Files of mode 0444 in a 0700 directory: containers read them by bind mount, other host users can't reach them."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def handle(self, name: str) -> SecretHandle:
        if not _NAME.fullmatch(name):
            raise ValueError(f"unsafe secret name: {name!r}")
        return SecretHandle(name)

    def path(self, handle: SecretHandle) -> Path:
        return self.directory / self.handle(handle.name).name

    def exists(self, name: str) -> bool:
        path = self.path(self.handle(name))
        return path.is_file() and path.stat().st_size > 0  # an empty file is a missing secret

    def read(self, handle: SecretHandle) -> str:
        if not self.exists(handle.name):
            raise KeyError(f"secret not set: {handle.name}")
        return self.path(handle).read_text(encoding="utf-8")

    def put(self, name: str, value: str) -> SecretHandle:
        handle = self.handle(name)
        if not value:
            raise ValueError(f"refusing to store an empty secret: {name}")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        fd, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(value)
            os.chmod(temporary, 0o444)
            os.replace(temporary, self.path(handle))
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return handle

    def ensure(self, name: str) -> SecretHandle:
        """Generate a random value only if none exists: first-start secrets must survive every re-run."""
        return self.handle(name) if self.exists(name) else self.put(name, secrets.token_urlsafe(48))

    def redact(self, text: str) -> str:
        if not self.directory.is_dir():
            return text
        for path in self.directory.iterdir():
            value = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
            if len(value) >= _MIN_REDACTED:
                text = text.replace(value, "***")
        return text
