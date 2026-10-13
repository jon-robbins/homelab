"""Test sessions used by local development and hosted CI."""

from pathlib import Path

import nox


ROOT = Path(__file__).parent
WORKERS = ROOT / "apps/homelab-workers"


def install(session: nox.Session, *packages: str) -> None:
    session.install(*packages)


@nox.session(tags=["no-docker"])
def workers(session: nox.Session) -> None:
    install(session, f"{WORKERS}[dev]")
    session.run("pytest", "-q", str(WORKERS / "src/homelab_workers/tests"))
    session.run("ruff", "check", str(WORKERS / "src"))


@nox.session(tags=["no-docker"])
def mcp(session: nox.Session) -> None:
    install(session, f"{WORKERS}", f"{ROOT / 'apps/homelab-mcp'}[dev]", "pytest-asyncio", "respx")
    session.chdir("apps/homelab-mcp")
    session.run("pytest", "-q")


@nox.session(tags=["no-docker"])
def homelab_agent(session: nox.Session) -> None:
    install(session, f"{ROOT / 'apps/homelab-agent'}[dev]")
    session.chdir("apps/homelab-agent")
    session.run("pytest", "-W", "ignore:The anyio.abc.BlockingPortal alias is deprecated:DeprecationWarning", "-q")
    session.run("ruff", "check", "app", "tests")


@nox.session(tags=["no-docker"])
def media_agent(session: nox.Session) -> None:
    install(session, f"{ROOT / 'apps/media-agent'}[dev]")
    session.run("pytest", "-W", "ignore:The anyio.abc.BlockingPortal alias is deprecated:DeprecationWarning", "-q", "apps/media-agent/tests")
    session.run("ruff", "check", "apps/media-agent/app", "apps/media-agent/tests")


@nox.session(tags=["no-docker"])
def release(session: nox.Session) -> None:
    session.install("--require-hashes", "-r", "config/release/requirements.txt")
    session.install("pytest")
    session.run("pytest", "-q", "tests/release")


@nox.session(tags=["docker"])
def compose_config(session: nox.Session) -> None:
    session.run("bash", "scripts/ci/compose-config-check.sh", external=True)


@nox.session(tags=["docker"])
def compose_tests(session: nox.Session) -> None:
    session.run("bash", "scripts/ci/run-compose-tests.sh", external=True)


@nox.session(tags=["no-docker"])
def ops(session: nox.Session) -> None:
    session.run("bash", "tests/ops/run_tests.sh", external=True)


@nox.session(tags=["no-docker"])
def qwen_static(session: nox.Session) -> None:
    install(session, "pytest")
    session.run("pytest", "-q", "apps/qwen-code-telegram/tests/test_response_contract.py")
    for test in ("test_entrypoint.sh", "test_role_render.sh", "test_system_policy.sh"):
        session.run("bash", f"apps/qwen-code-telegram/tests/{test}", external=True)


@nox.session(tags=["no-docker"])
def docs(session: nox.Session) -> None:
    session.run("bash", "tests/docs/run_tests.sh", external=True)
