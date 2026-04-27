from __future__ import annotations

import re

from .release_formatting import fold_for_match


def has_season_hint(name: str, season: int | None) -> bool:
    if season is None:
        return True
    low = (name or "").casefold()
    patterns = (
        f"s{season:02d}",
        f"s{season}",
        f"season {season}",
    )
    return any(p in low for p in patterns)


def season_range_includes(name: str, season: int) -> bool:
    low = (name or "").casefold()
    range_patterns = (
        r"\bs(?P<start>\d{1,2})\s*(?:-|to|through)\s*s?(?P<end>\d{1,2})\b",
        r"\bseasons?[\s._-]*(?P<start>\d{1,2})\s*(?:-|to|through)\s*(?P<end>\d{1,2})\b",
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
    norm = re.sub(r"[._-]+", " ", low)
    if "complete series" in norm or "complete collection" in norm:
        return True
    return False


def is_multi_season_pack(name: str) -> bool:
    low = (name or "").casefold()
    if re.search(r"\bs\d{1,2}\s*(?:-|to|through)\s*s?\d{1,2}\b", low):
        return True
    if re.search(r"\bseasons?\s*\d{1,2}\s*(?:-|to|through)\s*\d{1,2}\b", low):
        return True
    if "complete series" in low or "complete collection" in low:
        return True
    return False


def is_episode_specific_release(name: str) -> bool:
    low = (name or "").casefold()
    if re.search(r"\bs\d{1,2}\s*e\d{1,3}\b", low):
        return True
    if re.search(r"\b\d{1,2}x\d{1,3}\b", low):
        return True
    return False


def season_request_matches_release(name: str, season: int) -> bool:
    low = (name or "").casefold()
    if is_multi_season_pack(low):
        return season_range_includes(low, season)
    if is_episode_specific_release(low):
        return False
    if has_season_hint(low, season):
        return True
    return False


def season_path_matches(name: str, season: int) -> bool:
    low = (name or "").casefold()
    patterns = (
        rf"\bseason[\s._-]*0*{season}\b",
        rf"\bs0*{season}\b",
        rf"\b0*{season}x\d{{1,3}}\b",
        rf"\bs0*{season}e\d{{1,3}}\b",
    )
    return any(re.search(p, low) for p in patterns)


def extract_season_number(text: str) -> int | None:
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


def query_matches_torrent_name(query: str, torrent_name: str, season: int | None) -> bool:
    q = fold_for_match(query)
    n = fold_for_match(torrent_name)
    if not q or not n:
        return False
    season_ok = (
        season_request_matches_release(torrent_name, season)
        if isinstance(season, int)
        else has_season_hint(torrent_name, season)
    )
    if q in n and season_ok:
        return True
    q_tokens = [t for t in q.split() if len(t) >= 3]
    if len(q_tokens) < 2:
        return False
    if all(t in n for t in q_tokens) and season_ok:
        return True
    return False


__all__ = [
    "fold_for_match",
    "has_season_hint",
    "season_range_includes",
    "is_multi_season_pack",
    "is_episode_specific_release",
    "season_request_matches_release",
    "season_path_matches",
    "extract_season_number",
    "query_matches_torrent_name",
]
