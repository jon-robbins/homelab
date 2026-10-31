import json
from pathlib import Path
import stat

import pytest

from setup.engine.contract import Result, State
from setup.engine.local import Release, build_steps
from setup.engine.runner import run, status
from setup.tests.conftest import argv_text

PHASES = (("database", ("db",)), ("apps", ("*",)), ("caddy", ("caddy",)))


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


def test_phases_start_services_one_at_a_time_in_order(steps, ctx, ops):
    provide_web_key(ctx)
    outcome = converge(steps, ctx)
    assert outcome.ok, outcome.reason
    assert ops.ups() == [("homelab", "db"), ("homelab", "web"), ("homelab", "worker"), ("homelab-edge", "caddy")]


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


def test_an_exited_container_from_an_interrupted_first_run_is_started_again(steps, ctx, ops):
    provide_web_key(ctx)
    ops.containers[("homelab", "worker")] = "exited"  # no completed phase: not the owner's doing
    assert converge(steps, ctx).ok
    assert ("homelab", "worker") in ops.ups()


def test_a_service_that_never_becomes_healthy_fails_its_phase_and_stops_the_run(steps, ctx, ops):
    provide_web_key(ctx)
    ops.broken.add("db")
    outcome = converge(steps, ctx)
    assert not outcome.ok and outcome.stopped_at == "phase.database" and "db: unhealthy" in outcome.reason
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
    world["data"].mkdir(parents=True)  # e.g. the empty mount point of a disk that isn't mounted
    outcome = converge(steps, ctx)
    assert not outcome.ok and outcome.stopped_at == "engine.dirs"
    assert str(world["data"] / ".homelab-mounted") in outcome.reason


def test_a_new_root_gets_directories_and_its_marker(steps, ctx, world):
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    assert (world["data"] / "db").is_dir() and (world["data"] / ".homelab-mounted").is_file()


def test_service_stopped_in_site_is_created_but_never_started(ctx, models, ops):
    app, edge, site = models
    site["services"]["worker"] = {"state": "stopped"}
    steps = build_steps(
        ctx, Release.from_models(ctx.layout, app, edge, site), ops, phases=PHASES, generated=("DB_PASSWORD",)
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
    assert ops.calls == [] and not world["data"].exists() and not ctx.secrets.exists("DB_PASSWORD")


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
        ctx, release, ops, phases=(("database", ("db", "ghost")), ("apps", ("*",)), ("caddy", ("caddy",)))
    )
    assert [s.id for s in steps if s.id.startswith("phase.")] == ["phase.database", "phase.apps", "phase.caddy"]


def test_repeated_apply_never_restarts_an_owner_stopped_service(steps, ctx, ops):
    provide_web_key(ctx)
    assert converge(steps, ctx).ok
    ops.containers[("homelab", "web")] = "exited"
    ops.calls.clear()
    assert not converge(steps, ctx).ok
    assert not converge(steps, ctx).ok
    assert ops.ups() == []


def test_existing_stateful_service_is_snapshotted_before_recreation(steps, ctx, ops):
    provide_web_key(ctx)
    ops.containers[("homelab", "db")] = "running"
    original = ops.config_hash
    ops.config_hash = lambda directory, project, service: (
        "changed" if service == "db" else original(directory, project, service)
    )
    seen = []
    ops.stop = lambda service: seen.append(("stop", service))
    ops.snapshot = lambda service, tag: seen.append(("snapshot", service)) or "snapshot-id"
    original_run = ops.run

    def recording(argv):
        seen.append(("run", argv[-1]))
        original_run(argv)

    ops.run = recording
    assert converge(steps, ctx).ok
    assert seen[:3] == [("stop", "db"), ("snapshot", "db"), ("run", "db")]


def test_snapshot_failure_prevents_recreation(steps, ctx, ops):
    provide_web_key(ctx)
    ops.containers[("homelab", "db")] = "running"
    original = ops.config_hash
    ops.config_hash = lambda directory, project, service: (
        "changed" if service == "db" else original(directory, project, service)
    )
    ops.stop = lambda service: None

    def fail(service, tag):
        raise RuntimeError("snapshot unavailable")

    ops.snapshot = fail
    outcome = converge(steps, ctx)
    assert not outcome.ok and "snapshot unavailable" in outcome.reason
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
