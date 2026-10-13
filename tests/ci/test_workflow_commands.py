"""Smoke-test every Python command exposed by workflow run steps."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
COMMAND = re.compile(
    r"((?:\"[^\"]+/)?python(?:3)?(?:\"?\s+-m\s+scripts\.[\w.]+|\s+scripts/[\w./-]+\.py))"
)


def _workflow_runs() -> list[str]:
    runs: list[str] = []
    for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))

        def visit(value: object) -> None:
            if isinstance(value, dict):
                if isinstance(value.get("run"), str):
                    runs.append(value["run"])
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(document)
    return runs


def test_workflow_python_commands_support_help() -> None:
    commands = [match.group(1) for run in _workflow_runs() for match in COMMAND.finditer(run)]
    assert commands
    for command in commands:
        argv = shlex.split(command)
        if argv[0].endswith("/python") or argv[0].endswith("/python3"):
            argv[0] = sys.executable
        completed = subprocess.run(
            [*argv, "--help"], cwd=ROOT,
            capture_output=True, text=True, check=False,
        )
        assert completed.returncode == 0, f"{command}: {completed.stderr}"
