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
