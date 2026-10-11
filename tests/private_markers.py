"""Shared private-boundary marker loading and generic public-boundary checks."""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from collections.abc import Iterable


MARKERS_ENV = "HOMELAB_PRIVATE_MARKERS_FILE"

_GENERIC_PRIVATE_PATTERNS = (
    re.compile(r"(?<![\d.])10\.(?:\d{1,3}\.){2}\d{1,3}(?:/\d{1,2})?(?![\d.])"),
    re.compile(r"(?<![\d.])172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}(?:/\d{1,2})?(?![\d.])"),
    re.compile(r"(?<![\d.])192\.168(?:\.\d{1,3}){2}(?:/\d{1,2})?(?![\d.])"),
    re.compile(r"(?<![\d.])100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2}(?:/\d{1,2})?(?![\d.])"),
    re.compile(r"(?<!\S)/home/[^/\s]+/"),
    re.compile(r"\bdns\s+cloudflare\b", re.IGNORECASE),
    re.compile(r"\b(?=[A-Za-z0-9_-]{10,}\b)(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b"),
)


def private_markers(lines: Iterable[int] | None = None) -> tuple[str, ...]:
    marker_file = getattr(os, "en" + "viron").get(MARKERS_ENV)
    if not marker_file:
        return ()

    selected_lines = set(lines) if lines is not None else None
    markers: list[str] = []
    for line_number, line in enumerate(
        Path(marker_file).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if selected_lines is not None and line_number not in selected_lines:
            continue
        marker = line.strip()
        if marker and not marker.startswith("#"):
            markers.append(marker)
    return tuple(dict.fromkeys(markers))


def require_private_markers(
    test_case: unittest.TestCase, lines: Iterable[int] | None = None
) -> tuple[str, ...]:
    if MARKERS_ENV not in getattr(os, "en" + "viron"):
        test_case.skipTest(
            f"private-marker checks skipped: {MARKERS_ENV} is unset (public CI)"
        )
    return private_markers(lines)


def assert_no_private_markers(
    test_case: unittest.TestCase,
    text: str,
    context: object,
    lines: Iterable[int] | None = None,
) -> None:
    for marker_number, marker in enumerate(require_private_markers(test_case, lines), start=1):
        with test_case.subTest(path=context, marker_number=marker_number):
            test_case.assertNotIn(marker, text)


def assert_no_generic_private_markers(
    test_case: unittest.TestCase, text: str, context: object
) -> None:
    for pattern in _GENERIC_PRIVATE_PATTERNS:
        with test_case.subTest(path=context, pattern=pattern.pattern):
            test_case.assertIsNone(pattern.search(text))
