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

    parser.add_argument(
        "--qbittorrent-url",
        default=os.environ.get("QBITTORRENT_URL", "http://127.0.0.1:8080"),
        help="qBittorrent WebUI base URL",
    )
    parser.add_argument(
        "--qbittorrent-username",
        default=os.environ.get("QBITTORRENT_USERNAME", ""),
        help="qBittorrent username",
    )
    parser.add_argument(
        "--qbittorrent-password",
        default=os.environ.get("QBITTORRENT_PASSWORD", ""),
        help="qBittorrent password",
    )
    parser.add_argument(
        "--enable-health-replacement",
        action="store_true",
        help="Enable health-based replacement action for slow old torrents",
    )
    parser.add_argument(
        "--enable-health-race",
        action="store_true",
        help="Enable health race mode (keep current torrent and trigger one competitor search)",
    )
    parser.add_argument(
        "--health-replace-age-hours",
        type=float,
        default=float(os.environ.get("ARR_HEALTH_REPLACE_AGE_HOURS", "12")),
        help="Minimum torrent age in hours before replacement logic applies",
    )
    parser.add_argument(
        "--health-race-age-hours",
        type=float,
        default=float(os.environ.get("ARR_HEALTH_RACE_AGE_HOURS", "36")),
        help="Minimum torrent age in hours before race mode can be considered",
    )
    parser.add_argument(
        "--health-min-avg-speed-kib",
        type=float,
        default=float(os.environ.get("ARR_HEALTH_MIN_AVG_SPEED_KIB", "10")),
        help="Minimum average speed (KiB/s). Below this and past age threshold becomes unhealthy.",
    )
    parser.add_argument(
        "--health-skip-progress-percent",
        type=float,
        default=float(os.environ.get("ARR_HEALTH_SKIP_PROGRESS_PERCENT", "95")),
        help="Skip replacement if torrent progress is at or above this percent",
    )
    parser.add_argument(
        "--health-cooldown-hours",
        type=float,
        default=float(os.environ.get("ARR_HEALTH_COOLDOWN_HOURS", "12")),
        help="Cooldown in hours between health actions for the same item",
    )
    parser.add_argument(
        "--health-max-replacements-per-day",
        type=int,
        default=int(os.environ.get("ARR_HEALTH_MAX_REPLACEMENTS_PER_DAY", "2")),
        help="Maximum health replacements per item per day",
    )
    parser.add_argument(
        "--health-max-actions-per-sweep",
        type=int,
        default=int(os.environ.get("ARR_HEALTH_MAX_ACTIONS_PER_SWEEP", "8")),
        help="Global cap on health actions (replace/race) per script run",
    )
    parser.add_argument(
        "--health-min-seeders",
        type=int,
        default=int(os.environ.get("ARR_HEALTH_MIN_SEEDERS", "1")),
        help="Minimum seeders required among candidate releases before health action",
    )
    parser.add_argument(
        "--health-episode-id",
        type=int,
        action="append",
        default=[],
        help="Limit Sonarr health actions to these episode IDs (repeatable)",
    )
    parser.add_argument(
        "--health-movie-id",
        type=int,
        action="append",
        default=[],
        help="Limit Radarr health actions to these movie IDs (repeatable)",
    )
    parser.add_argument(
        "--health-state-file",
        default=os.environ.get("ARR_HEALTH_STATE_FILE", "/tmp/arr_retry_health_state.json"),
        help="Path to persistent health action state file",
    )
    return parser


def parse_args() -> argparse.Namespace:
    _load_project_dotenv()
    return build_parser().parse_args()
