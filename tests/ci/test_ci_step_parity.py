from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
LOCAL_GATE = ROOT / "scripts/ci/ci-local.sh"
SETUP_STEPS = {
    "Checkout",
    "Set up Python",
    "Create workers environment",
    "Create homelab agent environment",
    "Create media agent environment",
    "Create qwen code Telegram environment",
}


def _workflow_checks() -> dict[str, str]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    validate = workflow["jobs"]["validate"]
    return {
        step["name"]: _signature(step.get("run", ""), step["name"])
        for step in validate["steps"]
        if "name" in step
        and step["name"] not in SETUP_STEPS
        and not step["name"].startswith("Create ")
    }


def _workflow_step_names() -> set[str]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return {
        step["name"]
        for step in workflow["jobs"]["validate"]["steps"]
        if "name" in step
        and step["name"] not in SETUP_STEPS
        and not step["name"].startswith("Create ")
    }


def _local_checks() -> dict[str, str]:
    source = LOCAL_GATE.read_text(encoding="utf-8").replace("\\\n", " ")
    matches = re.finditer(
        r'^\s*run_(?:docker_)?step "([^"]+)"\s+(.+)$', source, re.MULTILINE
    )
    return {
        match.group(1): _signature(match.group(2), match.group(1))
        for match in matches
        if not match.group(1).startswith("$")
        if match.group(1) != "Build local images"
    }


def _local_step_names() -> set[str]:
    return set(_local_checks())


def test_validate_step_names_match_between_gates() -> None:
    assert _local_step_names() == _workflow_step_names()


def _signature(command: str, name: str) -> str:
    command = command.replace(str(ROOT), "")
    command = command.replace("${ROOT}/", "")
    command = command.replace("${WORKERS_DIR}", "apps/homelab-workers")
    command = command.replace("${GITHUB_WORKSPACE}", "").replace("${ROOT}", "")
    command = command.replace("${RUNNER_TEMP}", "VENV").replace("${CI_TMP}", "VENV")
    command = re.sub(r"(?:\$\{?\w+_VENV\}?|VENV/[^ ]+-venv)/bin", "VENV/bin", command)
    command = re.sub(r"['\"]", "", command)
    command = re.sub(r"set -euo pipefail\s*", "", command)
    command = re.sub(r"(?:bash -c\s+)?cd [^&\n]+(?:\s+&&)?\s*", "", command)
    command = re.sub(r"export PATH=[^&\n]+ &&\s*", "", command)
    command = re.sub(r"export PATH=[^\\\n]+(?:\\\n)?\s*", "", command)
    command = command.replace("${CI_PYTHON:-python3}", "python3")
    command = re.sub(r"PYTHONPATH=[^ ]+\s+", "", command)
    command = command.replace("&&", " ")
    command = command.replace("\\\n", " ")
    command = (
        command.replace("/apps/", "apps/")
        .replace("/tests/", "tests/")
        .replace("/scripts/", "scripts/")
    )
    if "bash -n" in command or "check_bash_syntax" in command:
        return "bash -n"
    if "mermaid" in command or name == "README mermaid blocks":
        return "README mermaid check"
    return " ".join(command.split())


def test_validate_step_names_and_commands_match_between_gates() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert "validate" in workflow["jobs"]
    workflow_checks = _workflow_checks()
    local_checks = _local_checks()

    assert local_checks == workflow_checks
    assert "--local" not in LOCAL_GATE.read_text(encoding="utf-8")


def test_local_gate_manages_python_before_ops_and_utility_steps() -> None:
    source = LOCAL_GATE.read_text(encoding="utf-8")
    workers_setup = source.index('python3 -m venv "$WORKERS_VENV"')
    ops_step = source.index('run_step "Ops shell tests"')
    utility_step = source.index('run_step "Public readiness"')
    assert workers_setup < ops_step
    assert workers_setup < utility_step
    assert 'export PATH="${WORKERS_VENV}/bin:${PATH}"' in source
    assert 'run_step "Public readiness" "${CI_PYTHON:-python3}"' in source
