"""Operator command line for the host controller."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import urllib.request

from scripts.controller.admission import load_host_policy
from scripts.controller.feed import FeedPolicy
from scripts.controller.planner import plan
from scripts.controller.reconcile import SubprocessOps, reconcile
from scripts.controller.state import State

ROOT = Path("/srv/homelab")


class HttpPinger:
    def __init__(self, trust: Path):
        env = (trust / "deadman.env").read_text()
        self.url = env.split("CONTROLLER_PING_URL=", 1)[1].splitlines()[0].strip().strip('"')

    def ok(self):
        urllib.request.urlopen(self.url, timeout=10).close()

    def fail(self, reason):
        request = urllib.request.Request(self.url + "/fail", data=reason.encode(), method="POST")
        urllib.request.urlopen(request, timeout=10).close()


def _pointer_sequence(path: Path) -> str:
    return path.resolve().name if path.exists() else "none"


def _args():
    parser = argparse.ArgumentParser(prog="homelabctl")
    parser.add_argument("--state", type=Path, default=ROOT / "state")
    parser.add_argument("--releases", type=Path, default=ROOT / "releases")
    parser.add_argument("--trust", type=Path, default=ROOT / "trust")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reconcile").add_argument("--now", action="store_true")
    sub.add_parser("status")
    sub.add_parser("ack").add_argument("--reason", required=True)
    sub.add_parser("rollback").add_argument("service")
    return parser.parse_args()


def _rollback(args, ops: SubprocessOps) -> int:
    previous = args.releases / "previous"
    services = json.loads((previous / "app.json").read_bytes()).get("services", {}) if previous.exists() else {}
    if args.service not in services:
        print(f"unknown service: {args.service}", file=sys.stderr)
        return 2
    if services[args.service].get("labels", {}).get("homelab.state") == "stateful":
        ops.stop(args.service)
        degraded = State(args.state).read("DEGRADED", "")
        snapshot = next(
            (line[len("snapshot: ") :] for line in degraded.splitlines() if line.startswith("snapshot: ")), "none"
        )
        tag = Path(snapshot).stem if snapshot != "none" else f"seq-{_pointer_sequence(args.releases / 'current')}"
        ops.restore(args.service, tag)
    ops.run(
        ["docker", "compose", "-p", "homelab", "-f", str(previous / "app.json"), "up", "-d", "--no-deps", args.service]
    )
    print("next: homelabctl ack --reason rollback complete")
    return 0


def _status(args, state: State) -> int:
    print((state.read("DEGRADED") or "ok").strip())
    pointers = {name: _pointer_sequence(args.releases / name) for name in ("current", "previous")}
    print(f"current={pointers['current']} previous={pointers['previous']} highwater={state.highwater()}")
    if (args.releases / "current").exists():
        p = plan(args.releases / "current", SubprocessOps())
        print(f"recreate={p.recreate} create_stopped={p.create_stopped} notices={p.notices} failures={p.failures}")
    return 0


def main() -> int:
    args = _args()
    state = State(args.state)
    if args.command == "rollback":
        return _rollback(args, SubprocessOps())
    if args.command == "ack":
        state.remove("DEGRADED")
        with (args.state / "ack.log").open("a") as handle:
            handle.write(f"{datetime.now(timezone.utc).isoformat()} {args.reason}\n")
        return 0
    if args.command == "status":
        return _status(args, state)
    policy = load_host_policy(args.trust / "policy.json")
    feed = FeedPolicy(str(policy.feed_directory), args.trust, policy)
    print(reconcile(state, feed, args.releases, SubprocessOps(), HttpPinger(args.trust), now=args.now))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
