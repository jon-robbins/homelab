"""Test sessions used by local development and hosted CI."""

import os
from pathlib import Path

import nox


PYTHON = "3.12"
nox.options.default_venv_backend = "uv|virtualenv"
ROOT = Path(__file__).parent
WORKERS = ROOT / "apps/homelab-workers"


def install(session: nox.Session, *packages: str) -> None:
    session.install(*packages)


@nox.session(python=PYTHON, tags=["no-docker"])
def complexity_budget(session: nox.Session) -> None:
    session.run("python", str(ROOT / "scripts/ci/complexity_budget.py"))


@nox.session(python=PYTHON, tags=["no-docker"])
def workers(session: nox.Session) -> None:
    install(session, f"{WORKERS}[dev]")
    session.run("pytest", "-q", str(WORKERS / "src/homelab_workers/tests"))
    session.run("ruff", "check", str(WORKERS / "src"))


@nox.session(python=PYTHON, tags=["no-docker"])
def release(session: nox.Session) -> None:
    session.install("--require-hashes", "-r", "config/release/requirements.txt")
    session.install("pytest")
    session.run("pytest", "-q", "tests/release", "--ignore=tests/release/test_render_docker.py")


@nox.session(python=PYTHON, tags=["no-docker"])
def controller(session: nox.Session) -> None:
    install(session, "pytest", "PyYAML")
    session.env["PYTHONPATH"] = str(ROOT)
    session.run("pytest", "-q", "tests/controller", "tests/migration")


@nox.session(python=PYTHON, tags=["docker"])
def controller_e2e(session: nox.Session) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        session.skip("controller_e2e runs in hosted CI only")
    install(session, "pytest", "PyYAML")
    session.env["PYTHONPATH"] = str(ROOT)
    session.run("pytest", "-q", "tests/integration/controller_e2e.py")


@nox.session(python=PYTHON, tags=["docker"])
def render_docker(session: nox.Session) -> None:
    session.install("--require-hashes", "-r", "config/release/requirements.txt")
    session.install("pytest")
    session.run("pytest", "-q", "tests/release/test_render_docker.py")


@nox.session(python=PYTHON, tags=["no-docker"])
def docs(session: nox.Session) -> None:
    session.run("bash", "tests/docs/run_tests.sh", external=True)
