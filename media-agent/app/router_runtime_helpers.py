from __future__ import annotations

import re
from typing import Any

from .models import RouterPendingOption, RouterSessionState
from .router_selection import canonical_option_id, parse_selection_rank


def _parse_selection_rank(user_message: str) -> int | None:
    return parse_selection_rank(user_message)


def _fold_for_match(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).split())


def _has_season_hint(name: str, season: int | None) -> bool:
    if season is None:
        return True
    low = (name or "").casefold()
    patterns = (
        f"s{season:02d}",
        f"s{season}",
        f"season {season}",
    )
    return any(p in low for p in patterns)


def _season_range_includes(name: str, season: int) -> bool:
    low = (name or "").casefold()
    range_patterns = (
        r"\bs(?P<start>\d{1,2})\s*(?:-|to|through)\s*s?(?P<end>\d{1,2})\b",
        r"\bseasons?\s*(?P<start>\d{1,2})\s*(?:-|to|through)\s*(?P<end>\d{1,2})\b",
    )
    for pattern in range_patterns:
        for match in re.finditer(pattern, low):
            try:
                start = int(match.group("start"))
                end = int(match.group("end"))
            except (TypeError, ValueError):
                continue
            if start <= season <= end or end <= season <= start:
                return True
    return False


def _is_multi_season_pack(name: str) -> bool:
    low = (name or "").casefold()
    if re.search(r"\bs\d{1,2}\s*(?:-|to|through)\s*s?\d{1,2}\b", low):
        return True
    if re.search(r"\bseasons?\s*\d{1,2}\s*(?:-|to|through)\s*\d{1,2}\b", low):
        return True
    if "complete series" in low or "complete collection" in low:
        return True
    return False


def _is_episode_specific_release(name: str) -> bool:
    low = (name or "").casefold()
    if re.search(r"\bs\d{1,2}\s*e\d{1,3}\b", low):
        return True
    if re.search(r"\b\d{1,2}x\d{1,3}\b", low):
        return True
    return False


def _season_request_matches_release(name: str, season: int) -> bool:
    low = (name or "").casefold()
    if _is_multi_season_pack(low):
        return False
    if _is_episode_specific_release(low):
        return False
    if _has_season_hint(low, season):
        return True
    return False


def _season_path_matches(name: str, season: int) -> bool:
    low = (name or "").casefold()
    patterns = (
        rf"\bseason[\s._-]*0*{season}\b",
        rf"\bs0*{season}\b",
        rf"\b0*{season}x\d{{1,3}}\b",
        rf"\bs0*{season}e\d{{1,3}}\b",
    )
    return any(re.search(p, low) for p in patterns)


def _extract_season_number(text: str) -> int | None:
    low = (text or "").casefold()
    matches: list[int] = []
    for pattern in (
        r"\bseason[\s._-]*0*(\d{1,2})\b",
        r"\bs0*(\d{1,2})e\d{1,3}\b",
        r"\b(\d{1,2})x\d{1,3}\b",
    ):
        for m in re.finditer(pattern, low):
            try:
                v = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if 0 <= v <= 99:
                matches.append(v)
    if matches:
        return matches[-1]
    return None


def _query_matches_torrent_name(query: str, torrent_name: str, season: int | None) -> bool:
    q = _fold_for_match(query)
    n = _fold_for_match(torrent_name)
    if not q or not n:
        return False
    season_ok = (
        _season_request_matches_release(torrent_name, season)
        if isinstance(season, int)
        else _has_season_hint(torrent_name, season)
    )
    if q in n and season_ok:
        return True
    q_tokens = [t for t in q.split() if len(t) >= 3]
    if len(q_tokens) < 2:
        return False
    if all(t in n for t in q_tokens) and season_ok:
        return True
    return False


def _build_pending_options(
    source_action: str, tool_result: dict[str, Any]
) -> list[RouterPendingOption]:
    pending: list[RouterPendingOption] = []
    options = tool_result.get("options") or []
    if not isinstance(options, list):
        return pending
    for op in options:
        if not isinstance(op, dict):
            continue
        try:
            rank = int(op.get("rank"))
        except (TypeError, ValueError):
            continue
        po = RouterPendingOption(
            rank=rank,
            option_id=canonical_option_id(
                source_action=source_action,
                rank=rank,
                title=str(op.get("title") or "(untitled)")[:500],
                guid=str(op.get("guid") or "") or None,
                episode_id=op.get("episode_id"),
                movie_id=op.get("movie_id"),
                release=op.get("release") if source_action == "indexer_search" else None,
            ),
            title=str(op.get("title") or "(untitled)")[:500],
            guid=str(op.get("guid") or "") or None,
            episode_id=op.get("episode_id"),
            movie_id=op.get("movie_id"),
            release=op.get("release") if source_action == "indexer_search" else None,
        )
        pending.append(po)
    return pending


def _selection_to_action_from_option(
    state: RouterSessionState, selected: RouterPendingOption
) -> dict[str, Any] | None:
    if state.source_action == "download_options_tv":
        if not selected.guid or selected.episode_id is None:
            return None
        return {
            "action": "download_grab_tv",
            "guid": selected.guid,
            "episode_id": selected.episode_id,
        }
    if state.source_action == "download_options_movie":
        if not selected.guid or selected.movie_id is None:
            return None
        return {
            "action": "download_grab_movie",
            "guid": selected.guid,
            "movie_id": selected.movie_id,
        }
    if state.source_action == "indexer_search":
        if not selected.release:
            return None
        return {"action": "indexer_grab", "release": selected.release}
    return None


def _selection_to_action(state: RouterSessionState, rank: int) -> dict[str, Any] | None:
    selected = next((x for x in state.options if x.rank == rank), None)
    if selected is None:
        return None
    return _selection_to_action_from_option(state, selected)
