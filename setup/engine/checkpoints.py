"""Durable run history: which steps finished, which were cut short, and the non-secret facts so far.

The truth about the host is always each step's `check()`; this file only records history, so a lost or stale
checkpoint costs a re-check, never a wrong skip. It never holds secret values.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
from typing import Any, Iterator

from scripts.controller.state import State

NAME = "checkpoints.json"


class AlreadyRunning(RuntimeError):
    pass


class Checkpoints:
    def __init__(self, directory: str | Path):
        self._files = State(directory)
        self._load()

    def _load(self) -> None:
        text = self._files.read(NAME)
        try:
            self._data: dict[str, Any] = json.loads(text) if text else {"version": 1, "steps": {}, "facts": {}}
        except ValueError as error:
            raise ValueError(
                f"{self._files.directory / NAME} is corrupt ({error}); move it aside to start over"
            ) from error

    @contextmanager
    def lock(self) -> Iterator[None]:
        with (self._files.directory / "setup.lock").open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise AlreadyRunning("another setup run is already running on this host") from error
            try:
                self._load()
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _save(self) -> None:
        self._files.write(NAME, json.dumps(self._data, indent=1, sort_keys=True) + "\n")

    def _mark(self, step: str, status: str, message: str = "") -> None:
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        completed = status == "done" or self.completed(step)
        self._data["steps"][step] = {"status": status, "message": message, "at": at, "completed": completed}
        self._save()

    def begin(self, step: str) -> None:
        self._mark(step, "started")

    def done(self, step: str, message: str = "") -> None:
        self._mark(step, "done", message)

    def failed(self, step: str, message: str) -> None:
        self._mark(step, "failed", message)

    def status(self, step: str) -> str | None:
        return self._data["steps"].get(step, {}).get("status")

    def completed(self, step: str) -> bool:
        entry = self._data["steps"].get(step, {})
        return entry.get("completed", entry.get("status") == "done")

    def message(self, step: str) -> str:
        return self._data["steps"].get(step, {}).get("message", "")

    def interrupted(self) -> tuple[str, ...]:
        return tuple(sorted(step for step, entry in self._data["steps"].items() if entry["status"] == "started"))

    def facts(self) -> dict[str, Any]:
        return dict(self._data["facts"])

    def set_fact(self, key: str, value: Any) -> None:
        self._data["facts"][key] = value
        self._save()
