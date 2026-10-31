import json

import pytest

from setup.engine.checkpoints import Checkpoints
from setup.engine.contract import (
    Confirm,
    Context,
    Result,
    Secret,
    State,
    Status,
    Text,
)
from setup.engine.layout import Layout
from setup.engine.runner import order, run, status
from setup.engine.secrets import SecretStore


class Fake:
    """A step whose world is a shared dict, so check() reflects what apply() did."""

    def __init__(self, id, world, needs=(), *, blocked=None, apply=None, verify_ok=True):
        self.id, self.needs, self.world = id, tuple(needs), world
        self.blocked, self.on_apply, self.verify_ok = blocked, apply, verify_ok
        self.applied = 0

    def check(self, ctx):
        if self.blocked:
            return State.blocked(self.blocked)
        return State.done() if self.world.get(self.id) else State.needed()

    def apply(self, ctx):
        self.applied += 1
        if self.on_apply:
            result = self.on_apply(ctx)
            if result is not None:
                return result
        self.world[self.id] = True
        return Result.success(f"{self.id} applied")

    def verify(self, ctx):
        return Result.success() if self.verify_ok else Result.failure("not actually working")


@pytest.fixture
def ctx(tmp_path):
    layout = Layout(tmp_path / "srv")
    layout.claim()
    return Context(
        layout=layout,
        secrets=SecretStore(tmp_path / "secrets"),
        checkpoints=Checkpoints(layout.setup_dir),
        platform=tmp_path,
        site=tmp_path / "site",
    )


def test_steps_run_in_dependency_order_not_list_order(ctx):
    world, seen = {}, []
    steps = [
        Fake("C", world, needs=["B"], apply=lambda c: seen.append("C")),
        Fake("A", world, apply=lambda c: seen.append("A")),
        Fake("B", world, needs=["A"], apply=lambda c: seen.append("B")),
    ]
    assert run(steps, ctx).ok
    assert seen == ["A", "B", "C"]


def test_unknown_need_and_cycles_are_refused_before_anything_runs(ctx):
    world = {}
    with pytest.raises(ValueError, match="unknown"):
        order([Fake("A", world, needs=["nope"])])
    with pytest.raises(ValueError, match="cycle"):
        order([Fake("A", world, needs=["B"]), Fake("B", world, needs=["A"])])
    with pytest.raises(ValueError, match="duplicate"):
        order([Fake("A", world), Fake("A", world)])


def test_done_steps_are_not_reapplied_and_a_second_run_changes_nothing(ctx):
    world = {}
    steps = [Fake("A", world), Fake("B", world, needs=["A"])]
    run(steps, ctx)
    run(steps, ctx)
    assert [s.applied for s in steps] == [1, 1]


def test_a_killed_run_resumes_where_it_stopped(ctx):
    world, killed = {}, {"once": True}

    def die(c):
        if killed.pop("once", False):
            raise KeyboardInterrupt  # SIGHUP/SIGKILL from a dropped SSH session looks the same to the next run

    steps = [Fake("A", world), Fake("B", world, needs=["A"], apply=die), Fake("C", world, needs=["B"])]
    with pytest.raises(KeyboardInterrupt):
        run(steps, ctx)
    assert ctx.checkpoints.interrupted() == ("B",)
    assert world == {"A": True}

    outcome = run(steps, ctx)
    assert outcome.ok and world == {"A": True, "B": True, "C": True}
    assert [s.applied for s in steps] == [1, 2, 1]
    assert ctx.checkpoints.interrupted() == ()


def test_work_finished_before_the_kill_is_verified_not_repeated(ctx):
    world = {"A": True}  # applied, but the run died before the checkpoint was written
    step = Fake("A", world)
    assert run([step], ctx).ok
    assert step.applied == 0
    assert ctx.checkpoints.status("A") == "done"


def test_drift_after_done_is_repaired_because_check_is_the_truth(ctx):
    world = {}
    step = Fake("A", world)
    run([step], ctx)
    world.clear()
    assert run([step], ctx).ok
    assert step.applied == 2


def test_blocked_stops_the_run_with_its_reason_and_skips_dependents(ctx):
    world = {}
    steps = [Fake("A", world, blocked="media disk missing"), Fake("B", world, needs=["A"])]
    outcome = run(steps, ctx)
    assert (outcome.ok, outcome.stopped_at, outcome.reason) == (False, "A", "media disk missing")
    assert steps[1].applied == 0


def test_failed_verify_is_a_failure_and_never_checkpointed_done(ctx):
    world = {}
    outcome = run([Fake("A", world, verify_ok=False)], ctx)
    assert not outcome.ok and "not actually working" in outcome.reason
    assert ctx.checkpoints.status("A") == "failed"


def test_an_exception_in_apply_is_a_failure_with_the_cause(ctx):
    def boom(c):
        raise RuntimeError("compose exploded")

    outcome = run([Fake("A", {}, apply=boom)], ctx)
    assert not outcome.ok and "compose exploded" in outcome.reason
    assert ctx.checkpoints.status("A") == "failed"


def test_prompts_are_answered_by_the_caller_and_the_step_reapplied(ctx):
    world = {}

    def need_domain(c):
        if "domain" not in c.answers:
            return Result.ask(Text("domain", "Your domain"))

    step = Fake("A", world, apply=need_domain)
    outcome = run([step], ctx, ask=lambda prompt: "example.test")
    assert outcome.ok and ctx.answers["domain"] == "example.test"
    assert ctx.checkpoints.answers() == {"domain": "example.test"}


def test_without_a_way_to_ask_a_prompt_blocks_the_step(ctx):
    step = Fake("A", {}, apply=lambda c: Result.ask(Confirm("ok", "Continue?")))
    outcome = run([step], ctx)
    assert not outcome.ok and "Continue?" in outcome.reason


def test_secret_answers_go_to_a_file_and_only_a_handle_is_kept(ctx):
    def need_token(c):
        if "TOKEN" not in c.answers:
            return Result.ask(Secret("TOKEN", "Paste the token"))

    run([Fake("A", {}, apply=need_token)], ctx, ask=lambda prompt: "tok-abcdefghijklmnop")
    assert ctx.secrets.read(ctx.answers["TOKEN"]) == "tok-abcdefghijklmnop"
    assert "tok-abcdefghijklmnop" not in (ctx.layout.setup_dir / "checkpoints.json").read_text()


def test_secret_values_never_reach_checkpoints_messages_or_the_outcome(ctx):
    value = ctx.secrets.read(ctx.secrets.ensure("DB_PASSWORD"))
    seen = []
    step = Fake("A", {}, apply=lambda c: Result.failure(f"login failed with {value}"))
    outcome = run([step], ctx, on_event=seen.append)
    blob = (
        json.dumps([outcome.reason, [e.message for e in seen]])
        + (ctx.layout.setup_dir / "checkpoints.json").read_text()
    )
    assert value not in blob


def test_facts_published_by_a_step_persist_for_later_steps_and_runs(ctx):
    step = Fake("A", {}, apply=lambda c: Result.success("joined", facts={"tailscale_ip": "192.0.2.9"}))
    run([step], ctx)
    assert ctx.facts["tailscale_ip"] == "192.0.2.9"
    assert ctx.checkpoints.facts()["tailscale_ip"] == "192.0.2.9"


def test_status_is_read_only_and_reports_every_step(ctx):
    world = {"A": True}
    steps = [Fake("A", world), Fake("B", world, needs=["A"])]
    report = status(steps, ctx)
    assert report["A"].status is Status.DONE and report["B"].status is Status.NEEDED
    assert [s.applied for s in steps] == [0, 0]
    assert ctx.checkpoints.status("A") is None


def test_a_concurrent_run_is_refused(ctx):
    other = Checkpoints(ctx.layout.setup_dir)
    with other.lock():
        outcome = run([Fake("A", {})], ctx)
    assert not outcome.ok and "already running" in outcome.reason


def test_facts_from_verify_recover_after_an_interrupted_apply(ctx):
    step = Fake("A", {"A": True})
    step.verify = lambda c: Result.success(facts={"external_id": "recovered"})
    assert run([step], ctx).ok
    assert ctx.checkpoints.facts() == {"external_id": "recovered"}


def test_secret_handle_answers_are_not_stored_as_literal_values(ctx):
    handle = ctx.secrets.put("TOKEN", "test-token-abcdefghijklmnop")

    def need_token(c):
        if "TOKEN" not in c.answers:
            return Result.ask(Secret("TOKEN", "Token"))

    assert run([Fake("A", {}, apply=need_token)], ctx, ask=lambda p: handle).ok
    assert ctx.secrets.read(handle) == "test-token-abcdefghijklmnop"


def test_sigkill_releases_lock_and_next_process_resumes(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import time

    script = tmp_path / "worker.py"
    script.write_text("""
import sys, time
from pathlib import Path
from setup.engine.checkpoints import Checkpoints
from setup.engine.contract import Context, Result, State
from setup.engine.layout import Layout
from setup.engine.runner import run
from setup.engine.secrets import SecretStore
root = Path(sys.argv[1])
layout = Layout(root)
layout.claim()
ctx = Context(layout, SecretStore(root / "secrets"), Checkpoints(layout.setup_dir), root, root)
class Work:
    id, needs = "work", ()
    def check(self, ctx):
        return State.done() if (root / "effect").exists() else State.needed()
    def apply(self, ctx):
        (root / "effect").write_text("once")
        if sys.argv[2] == "wait":
            (root / "ready").touch()
            time.sleep(60)
        return Result.success()
    def verify(self, ctx):
        return Result.success() if (root / "effect").read_text() == "once" else Result.failure("bad effect")
raise SystemExit(0 if run([Work()], ctx).ok else 1)
""")
    root = tmp_path / "host"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    process = subprocess.Popen([sys.executable, str(script), str(root), "wait"], env=env)
    try:
        deadline = time.monotonic() + 10
        while not (root / "ready").exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.02)
        assert (root / "ready").exists()
        process.kill()
        process.wait(timeout=5)
        assert Checkpoints(root / "setup").interrupted() == ("work",)
        subprocess.run([sys.executable, str(script), str(root), "resume"], env=env, check=True, timeout=10)
        assert Checkpoints(root / "setup").status("work") == "done"
        assert (root / "effect").read_text() == "once"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_nested_provider_facts_cannot_persist_a_known_secret(ctx):
    secret = ctx.secrets.read(ctx.secrets.ensure("TOKEN"))
    step = Fake("A", {}, apply=lambda c: Result.success(facts={"response": {"token": secret, "items": [secret]}}))
    assert run([step], ctx).ok
    assert secret not in (ctx.layout.setup_dir / "checkpoints.json").read_text()
    assert ctx.checkpoints.facts()["response"]["token"] == "***"


def test_secret_handle_on_a_text_prompt_is_a_clear_failure(ctx):
    handle = ctx.secrets.ensure("TOKEN")
    step = Fake("A", {}, apply=lambda c: Result.ask(Text("domain", "Domain")))
    outcome = run([step], ctx, ask=lambda p: handle)
    assert not outcome.ok and "Secret prompt" in outcome.reason
    assert ctx.checkpoints.answers() == {}
