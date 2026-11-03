import pytest

from setup import cli
from setup.engine import layout as layout_module
from setup.engine.local import Release


@pytest.fixture
def wired(monkeypatch, ops, models, world, tmp_path):
    """Run the real CLI against a fake Docker and a ready-made release instead of a rendered site."""
    monkeypatch.setattr(layout_module, "OWNER_ROOT", tmp_path / "owner")
    monkeypatch.setattr(layout_module, "UNIT_DIR", tmp_path / "units")
    app, edge, site = models
    world["data"].mkdir()
    (world["data"] / ".homelab-mounted").touch()
    (world["secrets"]).mkdir(parents=True)
    (world["secrets"] / "WEB_KEY").write_text("provided-by-a-provider-step")
    monkeypatch.setattr(cli, "LocalOps", lambda: ops)
    monkeypatch.setattr(cli, "preflight", lambda **kwargs: None)
    monkeypatch.setattr(cli, "install_docker", lambda: None)
    monkeypatch.setattr(cli, "apply_local_host_config", lambda: None)
    monkeypatch.setattr(
        cli,
        "open_site",
        lambda args, layout: (
            Release.from_models(layout, app, edge, site),
            world["secrets"],
        ),
    )
    monkeypatch.setattr(
        cli,
        "defaults",
        lambda: (
            (("database", ("db",)), ("apps", ("*",)), ("caddy", ("caddy",))),
            ("DB_PASSWORD",),
        ),
    )
    return [
        "--root",
        str(world["root"]),
        "--site",
        str(tmp_path / "site"),
        "--platform",
        str(tmp_path),
    ]


def tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")) if root.exists() else []


def test_apply_refuses_an_owner_pipeline_host_and_writes_nothing(wired, world, capsys):
    (world["root"] / "trust").mkdir(parents=True)
    (world["root"] / "trust" / "policy.json").write_text("{}")
    before = tree(world["root"])
    assert cli.main([*wired, "apply"]) == 2
    assert "signed-release" in capsys.readouterr().err
    assert tree(world["root"]) == before


def test_status_on_a_fresh_host_writes_nothing(wired, world, capsys):
    assert cli.main([*wired, "status"]) == 0
    assert "not set up" in capsys.readouterr().out
    assert not world["root"].exists()


def test_status_refuses_an_owner_pipeline_host(wired, world, capsys):
    (world["root"] / "trust").mkdir(parents=True)
    (world["root"] / "trust" / "policy.json").write_text("{}")
    assert cli.main([*wired, "status"]) == 2
    assert "signed-release" in capsys.readouterr().err


def test_the_real_owner_root_is_refused_even_with_another_root(wired, world, tmp_path, monkeypatch, capsys):
    owner = tmp_path / "real-srv-homelab"
    (owner / "trust").mkdir(parents=True)
    (owner / "trust" / "policy.json").write_text("{}")
    monkeypatch.setattr(layout_module, "OWNER_ROOT", owner)
    for command in ("apply", "status"):
        assert cli.main([*wired, command]) == 2
    assert not world["root"].exists()


def test_owner_compose_projects_that_setup_did_not_create_are_refused(wired, world, ops, capsys):
    ops.foreign = ["homelab"]
    assert cli.main([*wired, "apply"]) == 2
    assert "did not create: homelab" in capsys.readouterr().err
    assert not world["root"].exists() and ops.calls == []


def test_apply_then_status_reports_a_healthy_stack(wired, ops, capsys):
    assert cli.main([*wired, "apply"]) == 0
    capsys.readouterr()
    assert cli.main([*wired, "status"]) == 0
    out = capsys.readouterr().out
    assert "phase.caddy" in out and "needed" not in out and "blocked" not in out


def test_a_killed_apply_says_how_to_resume_and_resumes(wired, ops, world, capsys):
    original = ops.run
    state = {"killed": False}

    def killed_on_the_second_start(argv):
        if "up" in argv and argv[-1] == "web" and not state["killed"]:
            state["killed"] = True
            raise KeyboardInterrupt
        original(argv)

    ops.run = killed_on_the_second_start
    assert cli.main([*wired, "apply"]) == 130
    assert "run apply again" in capsys.readouterr().err
    started_before = ops.ups()
    assert cli.main([*wired, "apply"]) == 0
    assert ops.ups().count(("homelab", "db")) == 1  # the finished phase was not started again
    assert ("homelab", "db") in started_before


def test_status_lists_a_cut_short_step_so_the_person_knows(wired, ops, capsys):
    original = ops.run
    ops.run = lambda argv: ((_ for _ in ()).throw(KeyboardInterrupt) if "up" in argv else original(argv))
    cli.main([*wired, "apply"])
    capsys.readouterr()
    cli.main([*wired, "status"])
    assert "interrupted" in capsys.readouterr().out


@pytest.fixture
def prep(wired, monkeypatch, world, tmp_path):
    """`wired` with the host-preparation steps recorded, and no site argument (the host comes first)."""
    calls = []
    monkeypatch.setattr(cli, "preflight", lambda **kwargs: calls.append(("preflight", kwargs)))
    monkeypatch.setattr(cli, "install_docker", lambda: calls.append(("docker", {})))
    monkeypatch.setattr(cli, "apply_local_host_config", lambda: calls.append(("config", {})))
    return calls, ["--root", str(world["root"]), "--platform", str(tmp_path)]


def test_prepare_readies_the_host_without_a_site_and_claims_it(prep, world):
    calls, argv = prep

    assert cli.main([*argv, "prepare"]) == 0

    assert [name for name, _ in calls] == ["preflight", "docker", "config"]
    assert (world["root"] / "local-setup.owner").read_text().strip() == "local-setup"


def test_prepare_refuses_an_owner_host_before_installing_anything(prep, world, capsys):
    calls, argv = prep
    (world["root"] / "trust").mkdir(parents=True)
    (world["root"] / "trust" / "policy.json").write_text("{}")

    assert cli.main([*argv, "prepare"]) == 2

    assert "signed-release" in capsys.readouterr().err
    assert [name for name, _ in calls] == ["preflight"]


def test_rerunning_on_a_host_setup_already_serves_does_not_object_to_its_own_web_ports(prep):
    calls, argv = prep

    cli.main([*argv, "prepare"])
    cli.main([*argv, "prepare"])

    checks = [kwargs["check_ports"] for name, kwargs in calls if name == "preflight"]
    assert checks == [True, False]


def test_apply_and_status_still_need_a_site(prep, capsys):
    _, argv = prep

    with pytest.raises(SystemExit) as stopped:
        cli.main([*argv, "apply"])

    assert stopped.value.code == 2
    assert "--site" in capsys.readouterr().err
