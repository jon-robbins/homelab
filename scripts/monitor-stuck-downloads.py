#!/usr/bin/env python3
"""Monitor Radarr/Sonarr for stuck downloads and alert via Telegram."""

import json
import logging
import os
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HOMELAB_DIR = Path("/home/jon/homelab")
STATE_FILE = HOMELAB_DIR / "data" / "arr-retry" / "stuck-monitor-state.json"
LOG_FILE = HOMELAB_DIR / "data" / "arr-retry" / "stuck-monitor.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("stuck-monitor")

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
TIMEOUT = 5  # seconds per HTTP request

TELEGRAM_CHAT_ID = "6245395833"


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file (no quoting support needed)."""
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _read_xml_api_key(xml_path: Path) -> str:
    """Extract <ApiKey> from an *arr config.xml."""
    tree = ET.parse(xml_path)
    el = tree.find("ApiKey")
    if el is None or not el.text:
        raise RuntimeError(f"ApiKey not found in {xml_path}")
    return el.text.strip()


def load_config() -> dict[str, str]:
    """Return all config values needed by the script."""
    dotenv = _read_env_file(HOMELAB_DIR / ".env")
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN") or dotenv.get("TELEGRAM_BOT_TOKEN", "")
    if not telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not found in env or .env")

    radarr_key = _read_xml_api_key(HOMELAB_DIR / "data" / "radarr" / "config.xml")
    sonarr_key = _read_xml_api_key(HOMELAB_DIR / "data" / "sonarr" / "config.xml")

    return {
        "RADARR_URL": "http://127.0.0.1:7878/radarr",
        "RADARR_API_KEY": radarr_key,
        "SONARR_URL": "http://127.0.0.1:8989/sonarr",
        "SONARR_API_KEY": sonarr_key,
        "TELEGRAM_BOT_TOKEN": telegram_token,
    }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def api_get(url: str, api_key: str) -> Any:
    """GET JSON from an *arr API endpoint."""
    req = urllib.request.Request(
        url,
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def send_telegram(token: str, text: str) -> None:
    """Send an HTML-formatted Telegram message."""
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        log.info("Telegram alert sent successfully")
    except Exception as exc:
        log.error("Failed to send Telegram message: %s", exc)


# ---------------------------------------------------------------------------
# Resolution suggestions
# ---------------------------------------------------------------------------

def suggest_resolution(reason: str) -> str:
    """Map a rejection reason string to a human-friendly suggestion."""
    r = reason.lower()
    if "size" in r and ("limit" in r or "exceed" in r or "maximum" in r or "too large" in r):
        return "Increase max size in Settings → Quality for this quality tier"
    if "seeder" in r or "seed" in r:
        return "Wait for more seeders or try manual search"
    if "no indexer" in r or "indexer" in r and "available" in r:
        return "Check Prowlarr indexer health"
    if "no results" in r or "not found" in r or "not available" in r:
        return "Not available on configured indexers"
    if "custom format" in r or "score" in r:
        return "Adjust custom format scores in quality profile"
    if "quality" in r and ("not wanted" in r or "rejected" in r or "not allowed" in r):
        return "Enable this quality in the movie/series quality profile"
    return "Check logs for details"


def _format_size(size_bytes: float) -> str:
    """Human-readable size."""
    if size_bytes >= 1 << 30:
        return f"{size_bytes / (1 << 30):.2f}GB"
    if size_bytes >= 1 << 20:
        return f"{size_bytes / (1 << 20):.1f}MB"
    return f"{size_bytes:.0f}B"


# ---------------------------------------------------------------------------
# Radarr: stuck movies
# ---------------------------------------------------------------------------

def check_radarr(cfg: dict[str, str]) -> list[dict[str, Any]]:
    """Return list of stuck movie dicts."""
    url = cfg["RADARR_URL"]
    key = cfg["RADARR_API_KEY"]
    stuck: list[dict[str, Any]] = []

    try:
        movies = api_get(f"{url}/api/v3/movie", key)
    except Exception as exc:
        log.warning("Radarr unreachable: %s", exc)
        return stuck

    for m in movies:
        if not m.get("monitored", False):
            continue
        if m.get("hasFile", True):
            continue

        movie_id = m["id"]
        title = m.get("title", "Unknown")
        year = m.get("year", "")
        label = f"{title} ({year})" if year else title

        # Fetch cached releases for rejection reasons
        rejection_reason = ""
        try:
            releases = api_get(f"{url}/api/v3/release?movieId={movie_id}", key)
            if releases:
                # Find the first release with rejections, or summarise
                for rel in releases:
                    rejections = rel.get("rejections", [])
                    if rejections:
                        rejection_reason = rejections[0] if isinstance(rejections[0], str) else str(rejections[0])
                        # Add size context if it's a size rejection
                        if "size" in rejection_reason.lower():
                            size = rel.get("size", 0)
                            if size:
                                rejection_reason += f" ({_format_size(size)})"
                        break
                if not rejection_reason and releases:
                    rejection_reason = "No results found"
            else:
                rejection_reason = "No results found"
        except Exception as exc:
            log.debug("Could not fetch releases for %s: %s", label, exc)
            rejection_reason = "Could not fetch release info"

        suggestion = suggest_resolution(rejection_reason)
        stuck.append({
            "title": label,
            "source": "radarr",
            "reason": rejection_reason,
            "suggestion": suggestion,
        })

    log.info("Radarr: %d stuck movies", len(stuck))
    return stuck


# ---------------------------------------------------------------------------
# Sonarr: stuck episodes
# ---------------------------------------------------------------------------

def check_sonarr(cfg: dict[str, str]) -> list[dict[str, Any]]:
    """Return list of stuck series dicts (one entry per series with missing eps)."""
    url = cfg["SONARR_URL"]
    key = cfg["SONARR_API_KEY"]
    stuck: list[dict[str, Any]] = []

    try:
        wanted = api_get(f"{url}/api/v3/wanted/missing?pageSize=100&sortKey=airDateUtc&sortDirection=descending", key)
    except Exception as exc:
        log.warning("Sonarr unreachable: %s", exc)
        return stuck

    records = wanted.get("records", [])
    # Group by series
    series_map: dict[int, dict[str, Any]] = {}
    for ep in records:
        if not ep.get("monitored", False):
            continue
        sid = ep.get("seriesId", 0)
        series_title = ep.get("series", {}).get("title", "Unknown")
        season = ep.get("seasonNumber", 0)
        key_label = f"{series_title} S{season:02d}"

        if sid not in series_map:
            series_map[sid] = {
                "title": key_label,
                "source": "sonarr",
                "reason": "Missing episodes",
                "suggestion": "Wait for seeders or manual search",
                "missing_count": 0,
            }
        series_map[sid]["missing_count"] += 1

    stuck = list(series_map.values())
    log.info("Sonarr: %d stuck series", len(stuck))
    return stuck


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state() -> set[str]:
    """Load previous stuck titles from state file."""
    if not STATE_FILE.is_file():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text())
        return set(data.get("titles", []))
    except Exception:
        return set()


def save_state(titles: list[str]) -> None:
    """Persist current stuck titles."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "titles": titles,
        "updated": datetime.now(timezone.utc).isoformat(),
    }, indent=2))


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def build_message(stuck: list[dict[str, Any]], new_count: int) -> str:
    """Build HTML-formatted Telegram alert."""
    movies = [s for s in stuck if s["source"] == "radarr"]
    shows = [s for s in stuck if s["source"] == "sonarr"]

    lines = ["🔴 <b>New stuck downloads detected!</b>\n"]

    if movies:
        lines.append("<b>Movies (Radarr):</b>")
        for m in movies:
            reason = m["reason"] or "Unknown"
            lines.append(f"• {m['title']} — {reason} → {m['suggestion']}")
        lines.append("")

    if shows:
        lines.append("<b>Shows (Sonarr):</b>")
        for s in shows:
            extra = f" ({s['missing_count']} eps)" if s.get("missing_count") else ""
            lines.append(f"• {s['title']}{extra} — {s['reason']} → {s['suggestion']}")
        lines.append("")

    lines.append(f"<b>Total:</b> {len(stuck)} stuck items ({new_count} new)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=== Stuck download monitor run ===")

    try:
        cfg = load_config()
    except Exception as exc:
        log.error("Configuration error: %s", exc)
        sys.exit(1)

    prev_titles = load_state()

    # Gather stuck items
    stuck: list[dict[str, Any]] = []
    stuck.extend(check_radarr(cfg))
    stuck.extend(check_sonarr(cfg))

    current_titles = [s["title"] for s in stuck]
    new_titles = set(current_titles) - prev_titles
    new_count = len(new_titles)

    log.info("Total stuck: %d | New: %d | Previous: %d", len(stuck), new_count, len(prev_titles))

    # Send alert only if there are new stuck items
    if new_count > 0 and stuck:
        msg = build_message(stuck, new_count)
        send_telegram(cfg["TELEGRAM_BOT_TOKEN"], msg)
    else:
        log.info("No new stuck items — skipping Telegram alert")

    # Always update state
    save_state(current_titles)
    log.info("State saved with %d titles", len(current_titles))


if __name__ == "__main__":
    main()
