"""Test sessions used by local development and hosted CI."""

import json
import os
import subprocess
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
    session.run("pytest", "-q", "tests/release", "tests/modules", "--ignore=tests/release/test_render_docker.py")


@nox.session(python=PYTHON, tags=["no-docker"])
def controller(session: nox.Session) -> None:
    install(session, "pytest", "PyYAML")
    session.env["PYTHONPATH"] = str(ROOT)
    session.run("pytest", "-q", "tests/controller")


def _homelab_project_exists() -> bool:
    containers = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=homelab"],
        check=True, capture_output=True, text=True,
    ).stdout
    return bool(containers.strip())


@nox.session(python=PYTHON, tags=["docker"])
def controller_e2e(session: nox.Session) -> None:
    # The test brings up and tears down (down -v --remove-orphans) the "homelab" Compose
    # project, so it must never run against a Docker daemon that already has one.
    if os.environ.get("GITHUB_ACTIONS") != "true" and _homelab_project_exists():
        session.error("a 'homelab' Compose project exists on this Docker daemon; refusing to run")
    install(session, "pytest", "PyYAML")
    session.env["PYTHONPATH"] = str(ROOT)
    session.run("pytest", "-q", "tests/integration/controller_e2e.py")


@nox.session(python=PYTHON, tags=["docker"])
def render_docker(session: nox.Session) -> None:
    session.install("--require-hashes", "-r", "config/release/requirements.txt")
    session.install("pytest")
    session.run("pytest", "-q", "tests/release/test_render_docker.py")


@nox.session(python=PYTHON, tags=["docker"])
def images(session: nox.Session) -> None:
    """Build every first-party image locally, from the same list as CI's image-builds job."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        session.skip("hosted CI builds images in the image-builds job")
    session.install("--require-hashes", "-r", "config/release/requirements.txt")
    builds = session.run(
        "python", "-c",
        "import json, yaml; jobs = yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']; "
        "print(json.dumps(jobs['image-builds']['strategy']['matrix']['include']))",
        silent=True,
    )
    for index, build in enumerate(json.loads(builds)):
        tag = f"homelab-gate-image-{index}"
        session.run("docker", "build", "-q", "--file", build["dockerfile"], "--tag", tag,
                    build["context"], external=True)
        if build["cap_drop_check"] is True:
            session.run("docker", "run", "--rm", "--cap-drop", "ALL", "--security-opt",
                        "no-new-privileges:true", tag, "version", external=True)


@nox.session(python=PYTHON, tags=["no-docker"])
def docs(session: nox.Session) -> None:
    install(session, "pytest")
    session.run("pytest", "-q", "tests/docs")


@nox.session(python=PYTHON, tags=["no-docker"])
def host(session: nox.Session) -> None:
    install(session, "pytest")
    session.run("pytest", "-q", "tests/host")
