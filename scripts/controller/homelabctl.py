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


class HttpPinger:
    def __init__(self, trust: Path):
        self.url = (trust / "deadman.env").read_text().split("CONTROLLER_PING_URL=", 1)[1].splitlines()[0].strip().strip('"')
    def ok(self):
        urllib.request.urlopen(self.url, timeout=10).close()
    def fail(self, reason):
        request = urllib.request.Request(self.url + "/fail", data=reason.encode(), method="POST")
        urllib.request.urlopen(request, timeout=10).close()


def _pointer_sequence(path: Path) -> str:
    return path.resolve().name if path.exists() else "none"


def _args():
    parser = argparse.ArgumentParser(prog="homelabctl")
    parser.add_argument("--state", type=Path, default=Path("/var/lib/homelab-controller/state"))
    parser.add_argument("--releases", type=Path, default=Path("/var/lib/homelab-controller/releases"))
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--trust", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    reconcile_parser = sub.add_parser("reconcile")
    reconcile_parser.add_argument("--now", action="store_true")
    sub.add_parser("status")
    ack = sub.add_parser("ack")
    ack.add_argument("--reason", required=True)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("service")
    apply = sub.add_parser("apply")
    apply.add_argument("--edge", action="store_true")
    apply.add_argument("--takeover", action="store_true")
    return parser.parse_args()


def _rollback(args, ops: SubprocessOps) -> int:
    previous = args.releases / "previous"
    app = json.loads((previous / "app.json").read_bytes()) if previous.exists() else {}
    services = app.get("services", {})
    if args.service not in services:
        print(f"unknown service: {args.service}", file=sys.stderr)
        return 2
    service = services[args.service]
    if service.get("labels", {}).get("homelab.state") == "stateful":
        ops.stop(args.service)
        degraded = State(args.state).read("DEGRADED", "")
        snapshot = next((line.removeprefix("snapshot: ") for line in degraded.splitlines()
                         if line.startswith("snapshot: ")), "none")
        tag = Path(snapshot).stem if snapshot != "none" else f"seq-{_pointer_sequence(args.releases / 'current')}"
        ops.restore(args.service, tag)
    ops.run(["docker", "compose", "-p", "homelab", "-f", str(previous / "app.json"),
             "up", "-d", "--no-deps", args.service])
    print("next: homelabctl ack --reason rollback complete")
    return 0


def _apply_edge(args, ops: SubprocessOps) -> int:
    release = args.releases / "current"
    edge = json.loads((release / "edge.json").read_bytes())
    services = edge.get("services", {})
    names = tuple(services)
    if args.takeover:
        for service in names:
            ops.remove(service)
    if names:
        ops.run(["docker", "compose", "-p", "homelab-edge", "-f", str(release / "edge.json"),
                 "up", "-d", "--no-deps", *names])
        ops._project = "homelab-edge"
        for service in names:
            if not ops.healthy(service, 180):
                return 1
            if services[service].get("labels", {}).get("homelab.probe") and not ops.probe(service):
                return 1
    return 0


def main() -> int:
    args = _args()
    if args.command == "rollback":
        return _rollback(args, SubprocessOps())
    if args.command == "apply":
        return _apply_edge(args, SubprocessOps()) if args.edge else 0
    state = State(args.state)
    if args.command == "ack":
        state.remove("DEGRADED")
        with (args.state / "ack.log").open("a") as handle:
            handle.write(f"{datetime.now(timezone.utc).isoformat()} {args.reason}\n")
        return 0
    if args.command == "reconcile":
        if args.policy is None or args.trust is None:
            raise SystemExit("reconcile requires --policy and --trust")
        policy = load_host_policy(args.policy)
        result = reconcile(state, FeedPolicy(str(policy.feed_directory), args.trust, policy), args.releases,
                            SubprocessOps(), HttpPinger(args.trust), now=args.now)
        print(result)
        return 0
    degraded = state.read("DEGRADED")
    print(degraded.strip() if degraded else "ok")
    print(f"current={_pointer_sequence(args.releases / 'current')} "
          f"previous={_pointer_sequence(args.releases / 'previous')} highwater={state.highwater()}")
    current = args.releases / "current"
    if current.exists():
        ops = SubprocessOps()
        for service, definition in json.loads((current / "app.json").read_bytes()).get("services", {}).items():
            view = ops.container("homelab", service)
            image = definition.get("image", "") if isinstance(definition, dict) else ""; release_digest = image.rsplit("@", 1)[1] if "@" in image else "none"
            running_digest = view.image_digest if view is not None and view.image_digest else "none"; health = view.health if view is not None else "missing"
            print(f"{service}: running_digest={running_digest} release_digest={release_digest} health={health}")
        recorded_compose_version = state.read("compose-version")
        p = plan(current, ops, recorded_compose_version.strip() if recorded_compose_version is not None else None)
        print(f"recreate={p.recreate} create_stopped={p.create_stopped} notices={p.notices} hold={p.hold}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
