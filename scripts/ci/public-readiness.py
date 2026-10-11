#!/usr/bin/env python3
"""Find private-topology and private-marker values in tracked text files."""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


MARKERS_ENV = "HOMELAB_PRIVATE_MARKERS_FILE"
ALLOW_LIST = Path("scripts/ci/public-readiness-allow.txt")

IP_CANDIDATE = re.compile(
    r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?![\d.])"
)
HOME_PATH = re.compile(r"(?<!\S)/home/[^/\s]+/")
TOKEN_SHAPED = re.compile(
    r"\b(?=[A-Za-z0-9_-]{10,}\b)(?=[A-Za-z0-9_-]*[A-Z])"
    r"(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b"
)
SVG_TAG = re.compile(r"<(?P<contents>[A-Za-z][^<>]*)>")
SVG_ATTRIBUTE = re.compile(
    r"(?P<name>[A-Za-z_:][\w:.-]*)\s*=\s*"
    r"(?:\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)'|(?P<bare>[^\s>]+))"
)
# Mermaid stores edge geometry in data-points as well as the SVG points attribute.
SVG_GEOMETRY_ATTRIBUTES = {"d", "points", "data-points", "transform", "viewBox"}
SVG_GENERATED_IDENTIFIER = re.compile(r"(?:my-svg[-_]|L_)[A-Za-z0-9_-]+")

RULE_PRIVATE_NETWORK = "private-network"
RULE_HOME_PATH = "home-path"
RULE_TOKEN = "token-shaped"
RULE_MARKER = "private-marker"

# Addresses reserved for documentation are safe to show publicly.
DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("0.0.0.0/8", "127.0.0.0/8", "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
)


def tracked_paths(root: Path) -> Iterable[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    for raw_path in result.stdout.split(b"\0"):
        if raw_path:
            yield root / os.fsdecode(raw_path)


def load_markers(root: Path) -> tuple[str, ...]:
    marker_file = os.environ.get(MARKERS_ENV)
    if not marker_file:
        return ()
    values = []
    for line in Path(marker_file).read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            values.append(value)
    return tuple(dict.fromkeys(values))


def load_allow_list(root: Path) -> set[tuple[str, str]]:
    path = root / ALLOW_LIST
    if not path.exists():
        return set()
    allowed = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split(None, 1)
        if len(fields) != 2:
            raise ValueError(f"invalid allow-list entry: {line!r}")
        file_path, rule = fields
        if not file_path or not rule:
            raise ValueError(f"invalid allow-list entry: {line!r}")
        allowed.add((file_path, rule))
    return allowed


def private_network_match(value: str) -> bool:
    try:
        address = ipaddress.ip_interface(value).ip
    except ValueError:
        return False
    if any(address in network for network in DOCUMENTATION_NETWORKS):
        return False
    return any(address in network for network in PRIVATE_NETWORKS)


def svg_attribute_values(text: str, names: set[str]) -> Iterable[tuple[str, int, str]]:
    for tag in SVG_TAG.finditer(text):
        for attribute in SVG_ATTRIBUTE.finditer(tag.group("contents")):
            name = attribute.group("name")
            if name not in names:
                continue
            value_group = next(
                group for group in ("double", "single", "bare") if attribute.group(group) is not None
            )
            value_start = tag.start("contents") + attribute.start(value_group)
            yield name, value_start, attribute.group(value_group)


def svg_geometry_spans(text: str) -> tuple[tuple[int, int], ...]:
    return tuple(
        (value_start, value_start + len(value))
        for _, value_start, value in svg_attribute_values(text, SVG_GEOMETRY_ATTRIBUTES)
    )


def svg_generated_identifier_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans = []
    for _, value_start, value in svg_attribute_values(text, {"id", "data-id", "marker-end"}):
        spans.extend(
            (value_start + match.start(), value_start + match.end())
            for match in SVG_GENERATED_IDENTIFIER.finditer(value)
        )
    return tuple(spans)


def findings(root: Path) -> list[tuple[str, int, str]]:
    allowed = load_allow_list(root)
    markers = load_markers(root)
    results = []
    for path in tracked_paths(root):
        try:
            text = path.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relative = path.relative_to(root).as_posix()

        matches: list[tuple[int, str]] = []
        for match in IP_CANDIDATE.finditer(text):
            if private_network_match(match.group()):
                matches.append((match.start(), RULE_PRIVATE_NETWORK))
        matches.extend((match.start(), RULE_HOME_PATH) for match in HOME_PATH.finditer(text))
        geometry_spans = svg_geometry_spans(text) if path.suffix.lower() == ".svg" else ()
        generated_identifier_spans = (
            svg_generated_identifier_spans(text) if path.suffix.lower() == ".svg" else ()
        )
        matches.extend(
            (match.start(), RULE_TOKEN)
            for match in TOKEN_SHAPED.finditer(text)
            if not any(start <= match.start() < end for start, end in geometry_spans)
            and not any(start <= match.start() < end for start, end in generated_identifier_spans)
        )
        for marker in markers:
            cursor = 0
            while (index := text.find(marker, cursor)) != -1:
                matches.append((index, RULE_MARKER))
                cursor = index + max(len(marker), 1)

        for index, rule in sorted(matches):
            line_number = text.count("\n", 0, index) + 1
            if (relative, rule) not in allowed:
                results.append((relative, line_number, rule))
    return results


def main() -> int:
    root = Path.cwd()
    result = findings(root)
    for path, line, rule in result:
        print(f"{path}:{line}:{rule}")
    return 1 if result else 0


if __name__ == "__main__":
    sys.exit(main())
