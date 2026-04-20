from __future__ import annotations

import argparse
import os
from pathlib import Path


def _load_dotenv_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
            if "=" not in line:
                continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _load_project_dotenv() -> None:
    # Prefer current working directory for local runs; also try repo root relative
    # to this module so imports still work when cwd differs.
    candidate_paths = (
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    )
    for path in candidate_paths:
        _load_dotenv_file(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect/retry stuck Sonarr and Radarr items")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")

    parser.add_argument(
        "--sonarr-url",
        default=os.environ.get("SONARR_URL", "http://127.0.0.1:8989/sonarr"),
        help="Sonarr base URL",
    )
    parser.add_argument(
        "--sonarr-api-key",
        default=os.environ.get("SONARR_API_KEY", ""),
        help="Sonarr API key (or SONARR_API_KEY)",
    )
    parser.add_argument(
        "--sonarr-config-path",
        default=os.environ.get("SONARR_CONFIG_PATH", "data/sonarr/config.xml"),
        help="Path to Sonarr config.xml for API key fallback",
    )
    parser.add_argument(
        "--series-id",
        type=int,
        action="append",
        default=[],
        help="Limit Sonarr processing to these series IDs (repeatable)",
    )

    parser.add_argument(
        "--radarr-url",
        default=os.environ.get("RADARR_URL", "http://127.0.0.1:7878"),
        help="Radarr base URL",
    )
    parser.add_argument(
        "--radarr-api-key",
        default=os.environ.get("RADARR_API_KEY", ""),
        help="Radarr API key (or RADARR_API_KEY)",
    )
    parser.add_argument(
        "--radarr-config-path",
        default=os.environ.get("RADARR_CONFIG_PATH", "data/radarr/config.xml"),
        help="Path to Radarr config.xml for API key fallback",
    )
    parser.add_argument(
        "--movie-id",
        type=int,
        action="append",
        default=[],
        help="Limit Radarr processing to these movie IDs (repeatable)",
    )
    parser.add_argument(
        "--no-radarr",
        action="store_true",
        help="Skip Radarr checks",
    )

    parser.add_argument(
        "--search-missing-monitored",
        action="store_true",
        default=True,
        help="Search monitored missing items (default: enabled)",
    )
    parser.add_argument(
        "--no-search-missing-monitored",
        action="store_false",
        dest="search_missing_monitored",
        help="Disable monitored-missing searches",
    )
    parser.add_argument(
        "--min-seeders",
        type=int,
        default=1,
        help="Seeders threshold for 'has trackers available' (default: %(default)s)",
    )
    parser.add_argument(
        "--max-searches",
        type=int,
        default=100,
        help="Cap total search commands per app (default: %(default)s)",
    )
    parser.add_argument(
        "--max-missing-checks",
        type=int,
        default=20,
        help="Cap monitored-missing items checked for release availability per app",
    )
    parser.add_argument(
        "--http-timeout-seconds",
        type=int,
        default=20,
        help="HTTP timeout for Arr API calls",
    )
    parser.add_argument(
        "--allow-force-grab-fallback",
        action="store_true",
        help=(
            "When no approved release exists but seeded releases are "
            "rejected only by size/quality/cutoff reasons, force-grab best-seeded release."
        ),
    )
    return parser


def parse_args() -> argparse.Namespace:
    _load_project_dotenv()
    return build_parser().parse_args()
