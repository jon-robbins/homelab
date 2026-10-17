"""Operator command line for the host controller."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
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
    return parser.parse_args()


def main() -> int:
    args = _args()
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
