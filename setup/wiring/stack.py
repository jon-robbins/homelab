"""Run commands in the Authentik containers: no published port, and no value ever goes into argv or a log."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

PROJECT = "homelab"
POSTGRES, SERVER, WORKER = "authentik-postgres", "authentik-server", "authentik-worker"


class StackError(RuntimeError):
    pass


@dataclass(frozen=True)
class Stack:
    model: Path  # the release's app.json

    def _compose(self, *args: str) -> list[str]:
        return ["docker", "compose", "-p", PROJECT, "-f", str(self.model), *args]

    def call(self, argv: list[str], stdin: str | None = None) -> str:
        """Secrets travel on stdin only. Errors carry only the exit status; application output can contain secrets."""
        done = subprocess.run(argv, input=stdin, capture_output=True, text=True)
        if done.returncode:
            raise StackError(f"{' '.join(argv[6:8])} exited {done.returncode}")
        return done.stdout

    def once(self, service: str, *args: str, stdin: str | None = None, entrypoint: str | None = None) -> str:
        """A throwaway container from the service's definition, before (or without) the long-lived one."""
        flags = ["--entrypoint", entrypoint] if entrypoint else []
        return self.call(self._compose("run", "--rm", "--no-deps", "-T", *flags, service, *args), stdin)

    def exec(self, service: str, *args: str, stdin: str | None = None) -> str:
        return self.call(self._compose("exec", "-T", service, *args), stdin)

    def shell(self, code: str) -> str:
        """Run Python in `ak shell`. The script travels on stdin, so it may hold a one-time value."""
        return self.exec(SERVER, "ak", "shell", stdin=code)

    def psql(self, sql: str) -> list[list[str]]:
        out = self.exec(POSTGRES, "psql", "-U", "authentik", "-d", "authentik", "-tA", "-F", "|", "-c", sql)
        return [line.split("|") for line in out.splitlines() if line]

    def lifecycle(self, verb: str, service: str) -> None:
        """`docker compose start|stop|restart` for one service."""
        self.call(self._compose(verb, service))

    def healthy(self, service: str) -> bool:
        return (
            self.call(["docker", "inspect", "-f", "{{.State.Health.Status}}", self._id(service)]).strip() == "healthy"
        )

    def _id(self, service: str) -> str:
        return self.call(self._compose("ps", "-q", service)).strip()
