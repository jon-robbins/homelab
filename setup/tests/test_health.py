import pytest

from setup.engine.ops import unhealthy


def container(*, status="running", health=None, restarts=0):
    state = {"Status": status, **({"Health": {"Status": health}} if health else {})}
    return {"State": state, "RestartCount": restarts}


@pytest.mark.parametrize(
    ("inspected", "reason"),
    [
        (container(health="healthy"), ""),
        (container(), ""),  # no healthcheck: running is enough, as for `docker compose up --wait`
        (container(health="starting"), "health check is starting"),
        (container(health="unhealthy"), "health check is unhealthy"),
        (container(status="exited"), "container is exited"),
        (None, "no container"),
        (container(restarts=1), "restarting"),  # crash-looping containers are "running" half the time
    ],
)
def test_unhealthy_says_why(inspected, reason):
    assert unhealthy(inspected) == reason


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


def test_foreign_project_check_rejects_prefix_siblings_and_mixed_files(tmp_path, monkeypatch):
    import json
    import subprocess
    from setup.engine.ops import LocalOps

    root = tmp_path / "releases"
    projects = [
        {"Name": "homelab", "ConfigFiles": f"{root}-other/app.json"},
        {"Name": "homelab-edge", "ConfigFiles": f"{root}/abc/edge.json,/elsewhere/edge.json"},
        {"Name": "unrelated", "ConfigFiles": "/elsewhere/compose.yml"},
    ]
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps(projects))
    )
    assert LocalOps().foreign_projects(root) == ["homelab", "homelab-edge"]
    projects[:2] = [{"Name": "homelab", "ConfigFiles": f"{root}/abc/app.json"}]
    assert LocalOps().foreign_projects(root) == []
