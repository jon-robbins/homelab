import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from setup.engine.checkpoints import Checkpoints
from setup.engine.contract import Result, State
from setup.engine.layout import Layout
from setup.engine.local import Release, build_steps
from setup.engine.runner import run, status
from setup.engine.steps import Dirs, Network
from setup.tests.conftest import argv_text

PHASES = (("database", ("db",)), ("apps", ("*",)), ("caddy", ("caddy",)))


@pytest.fixture(autouse=True)
def disk(world):
    """The media disk is mounted: its root exists and carries the marker. Tests of the other case remove it."""
    world["data"].mkdir(parents=True)
    (world["data"] / ".homelab-mounted").touch()


@pytest.fixture
def release(ctx, models):
    app, edge, site = models
    return Release.from_models(ctx.layout, app, edge, site)


@pytest.fixture
def steps(ctx, release, ops):
    return build_steps(ctx, release, ops, phases=PHASES, generated=("DB_PASSWORD",))


def converge(steps, ctx, **kwargs):
    return run(steps, ctx, **kwargs)


def provide_web_key(ctx):
    ctx.secrets.put("WEB_KEY", "provided-by-a-provider-step")


def provide_existing_db_password(ctx):
    ctx.secrets.put("DB_PASSWORD", "existing-database-password")


@pytest.mark.parametrize(
    "port",
    [
        {"target": 8080, "published": "8080", "protocol": "tcp"},
        {"target": 8080, "published": "8080", "host_ip": "0.0.0.0"},
        {"target": 8080, "published": "8080", "host_ip": "8.8.4.4"},
    ],
)
def test_preview_rejects_published_ports_without_explicit_numeric_loopback(tmp_path, port):
    release = Release.from_models(
        Layout(tmp_path),
        {"services": {"web": {"ports": [port]}}},
        {"services": {}},
        {"services": {}},
    )

    with pytest.raises(ValueError, match="publishes a port"):
        release.require_loopback_published_ports()


def test_preview_accepts_only_explicit_loopback_ports_in_both_models(tmp_path):
    release = Release.from_models(
        Layout(tmp_path),
        {"services": {"web": {"ports": [{"target": 8080, "published": "8080", "host_ip": "127.0.0.1"}]}}},
        {"services": {"edge": {"ports": [{"target": 8443, "published": "8443", "host_ip": "::1"}]}}},
        {"services": {}},
    )

    release.require_loopback_published_ports()


def test_phases_start_services_one_at_a_time_in_order(steps, ctx, ops):
    provide_web_key(ctx)
    outcome = converge(steps, ctx)
    assert outcome.ok, outcome.reason
    assert ops.ups() == [
        ("homelab", "db"),
        ("homelab", "web"),
        ("homelab", "worker"),
        ("homelab-edge", "caddy"),
    ]


def test_every_start_is_no_deps_never_builds_and_never_pulls_implicitly(steps, ctx, ops):
    provide_web_key(ctx)
    converge(steps, ctx)
    for line in argv_text([c for c in ops.calls if "up" in c]):
        assert "--no-deps" in line and "--no-build" in line and "--pull never" in line


def test_images_are_pulled_before_they_are_started(steps, ctx, ops):
    provide_web_key(ctx)
    converge(steps, ctx)
    assert ("app", ("db",)) in ops.pulled and ("edge", ("caddy",)) in ops.pulled


def test_edge_services_run_from_the_edge_model(steps, ctx, ops, release):
    provide_web_key(ctx)
    converge(steps, ctx)
    edge = next(c for c in ops.calls if c[-1] == "caddy")
    assert edge[edge.index("-f") + 1] == str(release.directory / "edge.json")


def test_second_run_changes_nothing(steps, ctx, ops):
    provide_web_key(ctx)
    converge(steps, ctx)
    ops.calls.clear()
    ops.pulled.clear()
    assert converge(steps, ctx).ok
    assert ops.calls == [] and ops.pulled == []


def test_a_service_the_owner_stopped_afterwards_is_reported_not_restarted(steps, ctx, ops):
    provide_web_key(ctx)
    converge(steps, ctx)
    ops.containers[("homelab", "web")] = "exited"
    ops.calls.clear()
    outcome = converge(steps, ctx)
    assert not outcome.ok and "web" in outcome.reason and "stopped" in outcome.reason
    assert ops.ups() == []


def test_an_exited_container_is_never_restarted_even_before_the_phase_completed(steps, ctx, ops):
    provide_web_key(ctx)
    ops.containers[("homelab", "worker")] = "exited"  # a crash, or the owner's `docker stop`: setup can't tell which
    outcome = converge(steps, ctx)
    assert not outcome.ok and "worker" in outcome.reason and "docker rm" in outcome.reason
    assert ("homelab", "worker") not in ops.ups()


def test_a_lost_checkpoint_file_never_restarts_a_service_its_owner_stopped(steps, ctx, ops):
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    ops.containers[("homelab", "web")] = "exited"
    (ctx.layout.setup_dir / "checkpoints.json").unlink()  # moved aside as corrupt
    ctx.checkpoints = Checkpoints(ctx.layout.setup_dir)
    ops.calls.clear()
    assert not converge(steps, ctx).ok
    assert ops.ups() == []


def test_a_container_that_failed_to_start_is_started_again_not_blocked(steps, ctx, ops):
    provide_web_key(ctx)
    ops.containers[("homelab", "web")] = "created"
    ops.errors.add("web")  # Docker keeps State.Error, e.g. after a port was taken
    assert converge(steps, ctx).ok
    assert ("homelab", "web") in ops.ups()


def test_a_service_that_never_becomes_healthy_fails_its_phase_and_stops_the_run(steps, ctx, ops):
    provide_web_key(ctx)
    ops.broken.add("db")
    outcome = converge(steps, ctx)
    assert not outcome.ok and outcome.stopped_at == "phase.database" and "db is unhealthy" in outcome.reason
    assert ops.ups() == [("homelab", "db")]  # nothing after the failed phase was started


def test_generated_secrets_are_created_once_provider_secrets_are_not_invented(steps, ctx, ops):
    outcome = converge(steps, ctx)
    assert not outcome.ok and "WEB_KEY" in outcome.reason and "phase.apps" == outcome.stopped_at
    first = ctx.secrets.read(ctx.secrets.handle("DB_PASSWORD"))
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    assert ctx.secrets.read(ctx.secrets.handle("DB_PASSWORD")) == first
    assert not ctx.secrets.exists("SOMETHING_ELSE")


def test_a_media_root_that_exists_without_its_marker_blocks_instead_of_being_adopted(steps, ctx, world):
    (world["data"] / ".homelab-mounted").unlink()  # e.g. the empty mount point of a disk that isn't mounted
    outcome = converge(steps, ctx)
    assert not outcome.ok and outcome.stopped_at == "engine.dirs"
    assert str(world["data"] / ".homelab-mounted") in outcome.reason


def test_a_mounted_root_gets_its_directories(steps, ctx, world):
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    assert (world["data"] / "db").is_dir()


def test_a_missing_root_is_never_given_a_marker_on_the_system_disk(steps, ctx, world):
    shutil.rmtree(world["data"])  # the disk is not mounted, so its mount point does not exist either
    outcome = converge(steps, ctx)
    assert not outcome.ok and outcome.stopped_at == "engine.dirs"
    assert "mount" in outcome.reason and str(world["data"] / ".homelab-mounted") in outcome.reason
    assert not world["data"].exists()


def test_service_stopped_in_site_is_created_but_never_started(ctx, models, ops):
    app, edge, site = models
    site["services"]["worker"] = {"state": "stopped"}
    steps = build_steps(
        ctx,
        Release.from_models(ctx.layout, app, edge, site),
        ops,
        phases=PHASES,
        generated=("DB_PASSWORD",),
    )
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    assert ("homelab", "worker") not in ops.ups()
    assert any("--no-start" in c and c[-1] == "worker" for c in ops.calls)


def test_extra_steps_slot_into_the_order_through_requires(ctx, release, ops):
    seen = []

    class Gate:
        id, needs = "GATE-1", ("phase.apps",)

        def check(self, c):
            return State.needed()

        def apply(self, c):
            seen.append(ops.ups()[:])
            return Result.success()

        def verify(self, c):
            return Result.success()

    steps = build_steps(
        ctx,
        release,
        ops,
        phases=PHASES,
        generated=("DB_PASSWORD",),
        extra=[Gate()],
        requires={"phase.caddy": ("GATE-1",)},
    )
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    assert ("homelab-edge", "caddy") not in seen[0] and ("homelab", "web") in seen[0]


def test_status_reads_everything_and_changes_nothing(steps, ctx, ops, world):
    report = status(steps, ctx)
    assert report["phase.database"].status.value in ("needed", "blocked")
    assert ops.calls == [] and not (world["data"] / "db").exists() and not ctx.secrets.exists("DB_PASSWORD")


def test_release_directory_is_content_addressed_and_read_only(steps, ctx, release):
    provide_web_key(ctx)
    converge(steps, ctx)
    assert release.directory.parent == ctx.layout.releases
    for name in ("app.json", "edge.json", "site.yaml"):
        assert stat.S_IMODE((release.directory / name).stat().st_mode) == 0o444
    assert json.loads((release.directory / "app.json").read_text())["services"]["db"]


def test_unmanaged_site_manages_everything_it_runs(ctx, models, ops):
    app, edge, site = models
    site.pop("managed")
    release = Release.from_models(ctx.layout, app, edge, site)
    assert set(release.site["managed"]) == {"db", "web", "worker", "caddy"}


def test_services_in_a_phase_that_the_site_does_not_run_are_dropped(ctx, models, ops):
    app, edge, site = models
    release = Release.from_models(ctx.layout, app, edge, site)
    steps = build_steps(
        ctx,
        release,
        ops,
        phases=(("database", ("db", "ghost")), ("apps", ("*",)), ("caddy", ("caddy",))),
    )
    assert [s.id for s in steps if s.id.startswith("phase.")] == [
        "phase.database",
        "phase.apps",
        "phase.caddy",
    ]


def test_repeated_apply_never_restarts_an_owner_stopped_service(steps, ctx, ops):
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    ops.containers[("homelab", "web")] = "exited"
    ops.calls.clear()
    assert not converge(steps, ctx).ok
    assert not converge(steps, ctx).ok
    assert ops.ups() == []


def test_explicit_empty_managed_does_not_adopt_services(ctx, models, ops):
    app, edge, site = models
    site["managed"] = []
    release = Release.from_models(ctx.layout, app, edge, site)
    assert not any(s.id.startswith("phase.") for s in build_steps(ctx, release, ops))


@pytest.mark.parametrize("requires", [None, {"phase.tunnel": ()}])
def test_tunnel_cannot_start_without_an_external_gate_step(ctx, models, ops, requires):
    app, edge, site = models
    edge["services"]["cloudflared"] = {"image": "tunnel@sha256:" + "e" * 64}
    site["managed"].append("cloudflared")
    release = Release.from_models(ctx.layout, app, edge, site)
    with pytest.raises(ValueError, match="GATE-1"):
        converge(build_steps(ctx, release, ops, requires=requires), ctx)
    assert ops.calls == []


def changed(ops, service):
    original = ops.config_hash
    ops.config_hash = lambda directory, project, name: (
        "changed" if name == service else original(directory, project, name)
    )


def test_a_stateful_service_that_is_still_starting_is_waited_for_not_stopped(steps, ctx, ops):
    provide_web_key(ctx)
    provide_existing_db_password(ctx)
    ops.containers[("homelab", "db")] = "running"  # a killed run left it mid-start
    ops.starting.add("db")
    assert converge(steps, ctx).ok
    assert any("--wait" in c and c[-1] == "db" for c in ops.calls)  # waited out, not left failing


def test_the_first_start_of_a_stateful_service_is_allowed(steps, ctx, ops):
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    assert ("homelab", "db") in ops.ups()


def test_recreating_a_stateful_service_over_existing_data_stays_blocked(steps, ctx, ops):
    provide_web_key(ctx)
    provide_existing_db_password(ctx)
    ops.containers[("homelab", "db")] = "running"
    changed(ops, "db")
    outcome = converge(steps, ctx)
    assert not outcome.ok and "recreated over existing data" in outcome.reason
    assert ops.ups() == []
    assert not converge(steps, ctx).ok and ops.ups() == []


def test_existing_stateful_data_without_a_container_requires_a_manual_snapshot(steps, ctx, ops, world):
    provide_web_key(ctx)
    provide_existing_db_password(ctx)
    (world["data"] / "db").mkdir()
    (world["data"] / "db" / "existing.db").write_text("existing data")
    outcome = converge(steps, ctx)
    assert not outcome.ok and "recreated over existing data" in outcome.reason
    assert ("homelab", "db") not in ops.ups()


def test_stateful_recreation_stops_when_docker_cannot_be_inspected(steps, ctx, ops):
    provide_web_key(ctx)
    provide_existing_db_password(ctx)
    ops.inspect = lambda service, project="homelab": (_ for _ in ()).throw(OSError("Docker unavailable"))
    outcome = converge(steps, ctx)
    assert not outcome.ok and "Docker unavailable" in outcome.reason
    assert ops.ups() == []


def test_a_stopped_by_the_owner_stateful_service_is_still_left_alone(steps, ctx, ops):
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    ops.containers[("homelab", "db")] = "exited"
    ops.calls.clear()
    outcome = converge(steps, ctx)
    assert not outcome.ok and "db" in outcome.reason and ops.ups() == []


def test_new_data_directories_belong_to_the_user_the_service_runs_as(ctx, models, monkeypatch, world):
    app, edge, site = models
    app["services"]["db"]["user"] = "1000:1001"
    chowned = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: chowned.append((Path(path), uid, gid)))
    dirs = Dirs(Release.from_models(ctx.layout, app, edge, site))
    assert dirs.apply(ctx).ok
    assert chowned == [(world["data"] / "db", 1000, 1001)]


def test_without_root_a_directory_for_another_user_is_refused_before_anything_is_made(ctx, models, monkeypatch, world):
    app, edge, site = models
    app["services"]["db"]["user"] = "1000:1001"
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    monkeypatch.setattr(os, "getegid", lambda: 20)
    result = Dirs(Release.from_models(ctx.layout, app, edge, site)).apply(ctx)
    assert not result.ok and "1000:1001" in result.message and "root" in result.message
    assert not (world["data"] / "db").exists()


def test_apps_start_after_what_they_depend_on_not_alphabetically(ctx, models, ops):
    app, edge, site = models
    app["services"]["web"]["depends_on"] = {"worker": {"condition": "service_started"}}
    app["services"]["worker"]["depends_on"] = ["db"]
    steps = build_steps(
        ctx,
        Release.from_models(ctx.layout, app, edge, site),
        ops,
        phases=(("apps", ("*",)),),
        generated=("DB_PASSWORD",),
    )
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    started = [s for _, s in ops.ups()]
    assert started.index("db") < started.index("worker") < started.index("web")


def test_a_requires_entry_for_a_step_that_does_not_exist_is_an_error_not_ignored(ctx, release, ops):
    with pytest.raises(ValueError, match="phase.databse"):
        build_steps(ctx, release, ops, phases=PHASES, requires={"phase.databse": ("GATE-1",)})


def test_existing_stateful_edge_is_never_recreated_without_manual_backup(ctx, models, ops):
    app, edge, site = models
    edge["services"]["caddy"]["labels"] = {"homelab.state": "stateful"}
    ops.containers[("homelab-edge", "caddy")] = "running"
    changed(ops, "caddy")
    provide_web_key(ctx)
    steps = build_steps(
        ctx, Release.from_models(ctx.layout, app, edge, site), ops, phases=PHASES, generated=("DB_PASSWORD",)
    )
    outcome = converge(steps, ctx)
    assert not outcome.ok and "recreated over existing data" in outcome.reason
    assert ("homelab-edge", "caddy") not in ops.ups()


@pytest.mark.parametrize("lost_checkpoint", [False, True])
def test_a_generated_secret_lost_after_first_start_is_never_regenerated(steps, ctx, ops, world, lost_checkpoint):
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    if lost_checkpoint:
        (ctx.layout.setup_dir / "checkpoints.json").unlink()
        ctx.checkpoints = Checkpoints(ctx.layout.setup_dir)
    (world["secrets"] / "DB_PASSWORD").unlink()  # the secret directory is tmpfs: a reboot empties it
    outcome = converge(steps, ctx)
    assert not outcome.ok and outcome.stopped_at == "engine.secrets" and "DB_PASSWORD" in outcome.reason
    assert not ctx.secrets.exists("DB_PASSWORD")


def test_lost_secret_is_blocked_when_checkpoints_and_container_are_gone_but_data_remains(steps, ctx, ops, world):
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    (ctx.layout.setup_dir / "checkpoints.json").unlink()
    ctx.checkpoints = Checkpoints(ctx.layout.setup_dir)
    ops.containers.pop(("homelab", "db"))
    (world["data"] / "db" / "existing.db").write_text("existing data")
    (world["secrets"] / "DB_PASSWORD").unlink()
    outcome = converge(steps, ctx)
    assert outcome.stopped_at == "engine.secrets" and not ctx.secrets.exists("DB_PASSWORD")


def test_docker_inspection_error_never_means_a_missing_secret_is_safe_to_replace(steps, ctx, ops, world):
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    (ctx.layout.setup_dir / "checkpoints.json").unlink()
    ctx.checkpoints = Checkpoints(ctx.layout.setup_dir)
    (world["secrets"] / "DB_PASSWORD").unlink()
    ops.inspect = lambda service, project="homelab": (_ for _ in ()).throw(OSError("Docker unavailable"))
    outcome = converge(steps, ctx)
    assert not outcome.ok and "Docker unavailable" in outcome.reason
    assert not ctx.secrets.exists("DB_PASSWORD")


def test_new_service_can_generate_its_first_secret_after_other_phases_ran(steps, ctx, ops, models, world):
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    app, edge, site = models
    app["services"]["new"] = {"image": "new@sha256:" + "e" * 64, "secrets": [{"source": "NEW_PASSWORD"}]}
    app["secrets"]["NEW_PASSWORD"] = {"file": str(world["secrets"] / "NEW_PASSWORD")}
    site["managed"].append("new")
    added = build_steps(
        ctx,
        Release.from_models(ctx.layout, app, edge, site),
        ops,
        phases=PHASES,
        generated=("DB_PASSWORD", "NEW_PASSWORD"),
    )
    assert converge(added, ctx).ok
    assert ctx.secrets.exists("NEW_PASSWORD")


def test_a_homelab_net_that_setup_did_not_create_is_refused(ctx, ops):
    ops.network, ops.ours = True, False
    state = Network(ops).check(ctx)
    assert state.status.value == "blocked" and "not created by local setup" in state.reason
    assert not ops.calls


@pytest.mark.parametrize("service, required", [("authentik-server", "AK-1"), ("authentik-worker", "AK-0")])
def test_authentik_cannot_start_without_first_boot_wiring(ctx, ops, service, required):
    release = Release.from_models(ctx.layout, {"services": {service: {}}}, {}, {})
    steps = build_steps(ctx, release, ops)
    with pytest.raises(ValueError, match=required):
        run(steps, ctx)
    assert not ops.calls
