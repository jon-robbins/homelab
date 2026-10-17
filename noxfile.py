"""Test sessions used by local development and hosted CI."""

from pathlib import Path

import nox


# One interpreter everywhere; uv provides it locally when missing, CI's
# setup-python provides it there.
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
    install(session, "pytest")
    session.env["PYTHONPATH"] = str(ROOT)
    session.run("pytest", "-q", "tests/controller")


@nox.session(python=PYTHON, tags=["docker"])
def compose_config(session: nox.Session) -> None:
    session.run("bash", "scripts/ci/compose-config-check.sh", external=True)


@nox.session(python=PYTHON, tags=["docker"])
def compose_tests(session: nox.Session) -> None:
    session.run("bash", "scripts/ci/run-compose-tests.sh", external=True)


@nox.session(python=PYTHON, tags=["docker"])
def render_docker(session: nox.Session) -> None:
    session.install("--require-hashes", "-r", "config/release/requirements.txt")
    session.install("pytest")
    session.run("pytest", "-q", "tests/release/test_render_docker.py")


@nox.session(python=PYTHON, tags=["no-docker"])
def ops(session: nox.Session) -> None:
    session.run("bash", "tests/ops/run_tests.sh", external=True)


@nox.session(python=PYTHON, tags=["docker"])  # runs `docker compose config`
def docs(session: nox.Session) -> None:
    session.run("bash", "tests/docs/run_tests.sh", external=True)
