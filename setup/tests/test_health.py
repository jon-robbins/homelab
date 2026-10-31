from datetime import datetime, timedelta, timezone

import pytest

from setup.engine.ops import judge

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def container(*, status="running", health=None, restarts=0, age=60.0):
    state = {"Status": status, "StartedAt": (NOW - timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")}
    if health:
        state["Health"] = {"Status": health}
    return {"State": state, "RestartCount": restarts}


@pytest.mark.parametrize(
    ("inspected", "verdict"),
    [
        (container(health="healthy", age=1), "ok"),
        (container(health="starting"), "wait"),
        (container(health="unhealthy"), "fail"),
        (container(status="exited"), "fail"),
        (container(status="restarting"), "fail"),
        (None, "fail"),
        (container(age=2), "wait"),  # no healthcheck: must stay up for the settle time first
        (container(age=60), "ok"),
        (container(age=60, restarts=1), "fail"),  # crash-looping containers are "running" half the time
        (container(health="healthy", restarts=3), "ok"),  # a real probe outranks restart history
    ],
)
def test_judge(inspected, verdict):
    assert judge(inspected, 10, NOW).status == verdict


def test_failures_say_why():
    assert "exited" in judge(container(status="exited"), 10, NOW).reason
    assert "no container" in judge(None, 10, NOW).reason
    assert "unhealthy" in judge(container(health="unhealthy"), 10, NOW).reason


def test_commands_capture_both_streams_and_close_stdin(capsys):
    import sys
    from setup.engine.ops import LocalOps

    LocalOps().run(
        [
            sys.executable,
            "-c",
            "import sys; print('stdout'); print('stderr', file=sys.stderr); assert not sys.stdin.read()",
        ]
    )
    assert capsys.readouterr() == ("", "")


def test_foreign_container_check_rejects_prefix_siblings_and_mixed_files(tmp_path, monkeypatch):
    import subprocess
    from setup.engine.ops import LocalOps

    root = tmp_path / "releases"
    output = (
        f"ours\t{root}/abc/app.json\nforeign\t{root}-other/app.json\nmixed\t{root}/abc/app.json,/elsewhere/app.json\n"
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, output))
    assert LocalOps().foreign_containers(root) == ["foreign", "mixed"]
