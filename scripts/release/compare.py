"""Pre-merge check: what does changing the catalog or the site take away from a rendered site?

Renders one site twice, as it is now (OLD) and as it would be (NEW), and lists every difference per service. Losing a
port, GPU, device, group, capability, mount or secret, or a whole service, is an error: a site that stops passing
`gpus` or a Caddy port still renders and passes policy, so only a comparison sees it. Added and changed settings are
printed for review (for example a config digest after a script changed).

    python3 -m scripts.release.compare OLD_PLATFORM OLD_SITE NEW_PLATFORM NEW_SITE [--plugin NAME=DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
import sys
from typing import Any, Mapping

from .contracts import ValidationError
from .render import _plugin

KEPT = ("ports", "volumes", "secrets", "gpus", "devices", "group_add", "cap_add", "runtime", "deploy.resources")  # losing one of these is an error


def _facts(service: Mapping[str, Any]) -> set[tuple[str, str]]:
    facts = set()
    for field, value in service.items():
        if field == "ports":  # no address and 0.0.0.0 are the same listener
            value = [{**port, "host_ip": port.get("host_ip") or "0.0.0.0"} for port in value]
        if isinstance(value, dict):
            facts |= {(f"{field}.{key}", json.dumps(item, sort_keys=True)) for key, item in value.items()}
        elif field in KEPT and isinstance(value, list):
            facts |= {(field, json.dumps(item, sort_keys=True)) for item in value}
        else:
            facts.add((field, json.dumps(value, sort_keys=True)))
    return facts


def compare(old: Mapping[str, Mapping[str, Any]], new: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Return {service: {removed, added}} for every service that differs; `removed` of a vanished service is None."""
    report: dict[str, Any] = {}
    for name in sorted(old.keys() | new.keys()):
        if name not in new:
            report[name] = {"removed": None, "added": []}
            continue
        before, after = _facts(old.get(name, {})), _facts(new[name])
        if before != after:
            report[name] = {
                "removed": sorted(before - after),
                "added": sorted(after - before),
            }
    return report


def losses(report: Mapping[str, Any]) -> list[str]:
    found = []
    for name, change in report.items():
        if change["removed"] is None:
            found.append(f"{name}: service is gone")
        found += [f"{name}: lost {field} {value}" for field, value in change["removed"] or [] if field in KEPT]
    return found


def _services(platform: Path, site: Path, plugins: Mapping[str, Path]) -> dict[str, Any]:
    # Use each revision's renderer AND policy, not today's policy on yesterday's modules.
    command = [sys.executable, "-m", "scripts.release.render", str(site.resolve())]
    for name, path in plugins.items():
        command += ["--plugin", f"{name}={path.resolve()}"]
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(command, cwd=platform.resolve(), env=environment, capture_output=True, text=True)
    if result.returncode:
        raise ValidationError("render", result.stderr.strip())
    rendered = json.loads(result.stdout)
    return {**rendered["app"].get("services", {}), **rendered["edge"].get("services", {})}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.release.compare", description=__doc__.split("\n\n")[0])
    for name in ("old_platform", "old_site", "new_platform", "new_site"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--plugin", type=_plugin, action="append", default=[], help="NAME=DIR, once per plugin")
    args = parser.parse_args(argv)
    plugins = dict(args.plugin)
    try:
        report = compare(_services(args.old_platform, args.old_site, plugins),
                         _services(args.new_platform, args.new_site, plugins))
    except (ValidationError, OSError) as error:
        print(f"render failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    lost = losses(report)
    for line in lost:
        print(f"LOST {line}", file=sys.stderr)
    return 1 if lost else 0


if __name__ == "__main__":
    raise SystemExit(main())
