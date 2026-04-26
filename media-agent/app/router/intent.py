"""Pure intent / selection / season-prompt detection helpers.

These functions are deterministic, depend only on the user message, and
therefore live in their own module so they can be unit-tested in isolation
from the orchestrator.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from ..models.router import RouterIntentDecision, RouterPendingOption

_ORDINAL_MAP: dict[str, int] = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}


@dataclass(slots=True)
class SelectionChoice:
    rank: int | None = None
    option_id: str | None = None


def parse_selection_choice(user_message: str) -> SelectionChoice | None:
    text = (user_message or "").strip().lower()
    if not text:
        return None

    option_id_match = re.search(
        r"\b(?:option[\s_-]*id|id)\s*[:#]?\s*(opt-[a-z0-9-]{6,64})\b", text
    )
    if option_id_match:
        return SelectionChoice(option_id=option_id_match.group(1))

    option_id_direct_match = re.search(r"\b(opt-[a-z0-9-]{6,64})\b", text)
    if option_id_direct_match:
        return SelectionChoice(option_id=option_id_direct_match.group(1))

    if re.fullmatch(r"\d{1,2}", text):
        rank = int(text)
        if 1 <= rank <= 100:
            return SelectionChoice(rank=rank)
        return None

    for pattern in (
        r"\b(?:pick|choose|select|option|rank)\s*(?:#\s*)?(\d{1,2})\b",
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+option\b",
        r"\boption\s+(\d{1,2})\b",
    ):
        match = re.search(pattern, text)
        if not match:
            continue
        rank = int(match.group(1))
        if 1 <= rank <= 100:
            return SelectionChoice(rank=rank)

    for word, rank in _ORDINAL_MAP.items():
        if re.search(rf"\b{word}\s+option\b", text) or re.search(
            rf"\b(?:pick|choose|select)\s+{word}\b", text
        ):
            return SelectionChoice(rank=rank)

    return None


def parse_selection_rank(user_message: str) -> int | None:
    choice = parse_selection_choice(user_message)
    if choice is None:
        return None
    return choice.rank


def classify_intent(user_message: str, has_session_state: bool) -> RouterIntentDecision:
    selection = parse_selection_choice(user_message)
    if has_session_state and selection is not None:
        return RouterIntentDecision(
            intent="selection", reason="pending session has deterministic selection"
        )
    lower = (user_message or "").lower()
    media_words = (
        "download",
        "get ",
        "grab ",
        "movie",
        "show",
        "season",
        "episode",
        "indexer",
        "torrent",
    )
    if any(w in lower for w in media_words):
        return RouterIntentDecision(intent="download", reason="matched media keywords")
    return RouterIntentDecision(intent="non_media", reason="no media keywords matched")


def season_prompt_needed(message: str) -> bool:
    """User mentioned a season but didn't supply a number."""
    lower = (message or "").lower()
    return bool(
        re.search(r"\bseason\b", lower)
        and not re.search(r"\bseason\s+\d{1,2}\b", lower)
    )


def prefer_indexer_for_title_request(
    action_payload: dict[str, Any],
) -> dict[str, Any]:
    """Conversational title requests should search Prowlarr first.

    The explicit `/action` endpoint still exposes ``download_options_tv`` and
    ``download_options_movie`` for callers that want library semantics. The
    conversational router treats a title request as an acquisition request
    and rewrites the call to ``indexer_search`` directly.
    """
    action = str(action_payload.get("action") or "")
    if action == "download_options_tv":
        query = str(action_payload.get("query") or "").strip()
        season = action_payload.get("season")
        if isinstance(season, int):
            query = f"{query} season {season}".strip()
        return {
            "action": "indexer_search",
            "query": query,
            "limit": 10,
            "search_type": "search",
        }
    if action == "download_options_movie":
        query = str(action_payload.get("query") or "").strip()
        return {
            "action": "indexer_search",
            "query": query,
            "limit": 10,
            "search_type": "search",
        }
    return action_payload


def canonical_option_id(
    *,
    source_action: str,
    rank: int,
    title: str,
    guid: str | None,
    episode_id: int | None,
    movie_id: int | None,
    release: dict[str, Any] | None,
) -> str:
    release_guid = ""
    release_hash = ""
    if isinstance(release, dict):
        release_guid = str(release.get("guid") or "")
        release_hash = str(release.get("infoHash") or "")
    raw = "|".join(
        [
            source_action,
            str(rank),
            (guid or "").strip(),
            str(episode_id or ""),
            str(movie_id or ""),
            release_guid.strip(),
            release_hash.strip(),
            (title or "").strip().casefold(),
        ]
    )
    suffix = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"opt-{rank:02d}-{suffix}"


def resolve_pending_option(
    *,
    options: list[RouterPendingOption],
    selection: SelectionChoice,
) -> RouterPendingOption | None:
    if selection.option_id:
        for option in options:
            if option.option_id == selection.option_id:
                return option
    if isinstance(selection.rank, int):
        for option in options:
            if option.rank == selection.rank:
                return option
    return None


def build_pending_options(
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
        title = str(op.get("title") or "(untitled)")[:500]
        guid = str(op.get("guid") or "") or None
        episode_id = op.get("episode_id")
        movie_id = op.get("movie_id")
        release = op.get("release") if source_action == "indexer_search" else None
        pending.append(
            RouterPendingOption(
                rank=rank,
                option_id=canonical_option_id(
                    source_action=source_action,
                    rank=rank,
                    title=title,
                    guid=guid,
                    episode_id=episode_id,
                    movie_id=movie_id,
                    release=release,
                ),
                title=title,
                guid=guid,
                episode_id=episode_id,
                movie_id=movie_id,
                release=release,
            )
        )
    return pending


__all__ = [
    "SelectionChoice",
    "parse_selection_choice",
    "parse_selection_rank",
    "classify_intent",
    "season_prompt_needed",
    "prefer_indexer_for_title_request",
    "canonical_option_id",
    "resolve_pending_option",
    "build_pending_options",
]
