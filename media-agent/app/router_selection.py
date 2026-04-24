from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .models import RouterPendingOption

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
