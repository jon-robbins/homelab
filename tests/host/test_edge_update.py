from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "host/homelab-edge-update"

# A fake docker: logs every call, lists two edge services, and reports $FAKE_HEALTH for them.
DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
  *"config --services"*) printf 'caddy\\ncloudflared\\n' ;;
  *" pull "*) exit "${FAKE_PULL_STATUS:-0}" ;;
  "inspect -f {{.State.Status}}"*) echo running ;;
  "inspect -f"*) echo "${FAKE_HEALTH:-healthy}" ;;
  *"ps -q "*) for last; do :; done; printf 'id-%s\\n' "$last" ;;
esac
"""


def setup(tmp_path: Path, edge: str, *, good: str | None = None, previous: str | None = None) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "docker").write_text(DOCKER)
    (bin_dir / "docker").chmod(0o755)
    home = tmp_path / "srv"
    for name, text in (("current", edge), ("previous", previous)):
        if text is not None:
            (home / "releases" / name).mkdir(parents=True, exist_ok=True)
            (home / "releases" / name / "edge.json").write_text(text)
    (home / "state").mkdir(parents=True, exist_ok=True)
    if good is not None:
        (home / "state/edge-good.json").write_text(good)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOMELAB": str(home),
        "EDGE_HEALTH_TIMEOUT": "5",
        "FAKE_DOCKER_LOG": str(tmp_path / "docker.log"),
    }


def run(env: dict[str, str], **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(SCRIPT)], env={**env, **extra}, text=True, capture_output=True)


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "docker.log"
    return log.read_text().splitlines() if log.exists() else []


def ups(tmp_path: Path) -> list[str]:
    return [call for call in calls(tmp_path) if " up -d --no-deps " in call]


def state(tmp_path: Path, name: str) -> Path:
    return tmp_path / "srv/state" / name


def test_unchanged_edge_touches_nothing(tmp_path: Path):
    env = setup(tmp_path, '{"v": 1}', good='{"v": 1}')
    assert run(env).returncode == 0
    assert calls(tmp_path) == []


def test_new_release_is_pulled_applied_and_recorded_as_good(tmp_path: Path):
    env = setup(tmp_path, '{"v": 2}', good='{"v": 1}')
    result = run(env)
    assert result.returncode == 0, result.stderr
    assert any(" pull caddy cloudflared" in call for call in calls(tmp_path))
    assert len(ups(tmp_path)) == 1 and ups(tmp_path)[0].endswith("up -d --no-deps caddy cloudflared")
    assert state(tmp_path, "edge-good.json").read_text() == '{"v": 2}'
    assert not state(tmp_path, "edge-failed").exists()


def test_unhealthy_release_rolls_back_to_last_good_and_is_not_retried(tmp_path: Path):
    env = setup(tmp_path, '{"v": 2}', good='{"v": 1}')
    result = run(env, FAKE_HEALTH="unhealthy")
    assert result.returncode == 1
    assert "rolled back" in result.stderr
    applied, rolled_back = ups(tmp_path)
    assert "edge-good.json" in rolled_back and "edge-good.json" not in applied
    assert state(tmp_path, "edge-good.json").read_text() == '{"v": 1}'
    assert state(tmp_path, "edge-failed").exists()

    (tmp_path / "docker.log").unlink()
    assert run(env, FAKE_HEALTH="healthy").returncode == 0
    assert calls(tmp_path) == []


def test_newer_release_after_a_failure_is_tried_and_clears_the_skip(tmp_path: Path):
    env = setup(tmp_path, '{"v": 2}', good='{"v": 1}')
    assert run(env, FAKE_HEALTH="unhealthy").returncode == 1
    (tmp_path / "srv/releases/current/edge.json").write_text('{"v": 3}')
    assert run(env).returncode == 0
    assert state(tmp_path, "edge-good.json").read_text() == '{"v": 3}'
    assert not state(tmp_path, "edge-failed").exists()


def test_pull_failure_changes_nothing_and_retries_next_run(tmp_path: Path):
    env = setup(tmp_path, '{"v": 2}', good='{"v": 1}')
    result = run(env, FAKE_PULL_STATUS="1")
    assert result.returncode == 1 and "retrying next run" in result.stderr
    assert ups(tmp_path) == []
    assert not state(tmp_path, "edge-failed").exists()
    assert run(env).returncode == 0
    assert state(tmp_path, "edge-good.json").read_text() == '{"v": 2}'


def test_first_run_falls_back_to_the_previous_release(tmp_path: Path):
    env = setup(tmp_path, '{"v": 2}', previous='{"v": 1}')
    assert run(env, FAKE_HEALTH="unhealthy").returncode == 1
    assert "releases/previous/edge.json" in ups(tmp_path)[-1]
    assert not state(tmp_path, "edge-good.json").exists()


def test_failure_with_nothing_to_roll_back_to_still_fails_loudly(tmp_path: Path):
    env = setup(tmp_path, '{"v": 2}')
    result = run(env, FAKE_HEALTH="unhealthy")
    assert result.returncode == 1 and "nothing to roll back to" in result.stderr
    assert len(ups(tmp_path)) == 1


def test_observe_only_and_missing_release_do_nothing(tmp_path: Path):
    env = setup(tmp_path, '{"v": 2}', good='{"v": 1}')
    state(tmp_path, "observe-only").touch()
    assert run(env).returncode == 0
    state(tmp_path, "observe-only").unlink()
    (tmp_path / "srv/releases/current/edge.json").unlink()
    assert run(env).returncode == 0
    assert calls(tmp_path) == []
