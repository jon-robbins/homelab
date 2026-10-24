from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Mapping

from homelab_mcp.redaction import redact_for_response

MAX_PEERS = 25
MAX_TRACKERS = 25
MAX_MATCHES = 25
MAX_OPTIONS = 25


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    source: str
    checked_at: str
    status: str
    facts: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def make_evidence(source: str, *, status: str, facts: Mapping[str, object]) -> EvidenceRecord:
    return EvidenceRecord(
        source=str(source),
        checked_at=_now_iso(),
        status=str(status),
        facts=redact_for_response(dict(facts)),
    )


def compact_tool_result(tool_name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
    result: dict[str, object] = dict(payload)
    if tool_name == "qbt_torrent_detail":
        peers = result.get("peers")
        if isinstance(peers, list):
            result.setdefault("peer_count", len(peers))
            result["peers"] = peers[:MAX_PEERS]
        trackers = result.get("trackers")
        if isinstance(trackers, list):
            result["trackers"] = trackers[:MAX_TRACKERS]
    elif tool_name == "plex_library_search":
        matches = result.get("matches")
        if isinstance(matches, list):
            result["matches"] = matches[:MAX_MATCHES]
    elif tool_name == "plex_watch_status":
        show_progress = result.get("show_progress")
        if isinstance(show_progress, dict):
            seasons = show_progress.get("seasons")
            if isinstance(seasons, list):
                compact_seasons: list[dict[str, object]] = []
                for season in seasons[:MAX_MATCHES]:
                    if not isinstance(season, dict):
                        continue
                    season_copy = dict(season)
                    episodes = season_copy.get("episodes")
                    if isinstance(episodes, list):
                        season_copy["episodes"] = episodes[:MAX_MATCHES]
                    compact_seasons.append(season_copy)
                show_progress = dict(show_progress)
                show_progress["seasons"] = compact_seasons
                result["show_progress"] = show_progress
    elif tool_name in {"radarr_release_search", "sonarr_release_search", "prowlarr_indexer_search"}:
        options = result.get("options")
        if isinstance(options, list):
            result["options"] = options[:MAX_OPTIONS]
    return result
