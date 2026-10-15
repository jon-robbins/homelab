"""Atomic filesystem state for the M3a controller."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import tempfile
from typing import Iterator


class State:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.directory / "lock"

    @contextmanager
    def lock(self) -> Iterator["State"]:
        with self.lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield self
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read(self, name: str, default: str | None = None) -> str | None:
        path = self.directory / name
        try:
            return path.read_text()
        except FileNotFoundError:
            return default

    def write(self, name: str, value: str) -> None:
        with self.lock():
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=self.directory)
            try:
                with os.fdopen(fd, "w") as handle:
                    handle.write(value)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.directory / name)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise

    def remove(self, name: str) -> None:
        with self.lock():
            (self.directory / name).unlink(missing_ok=True)

    def highwater(self) -> int:
        return int(self.read("highwater", "0").strip())

    def set_highwater(self, sequence: int) -> None:
        self.write("highwater", f"{sequence}\n")
