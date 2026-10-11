from __future__ import annotations

import re

from homelab_workers.shared.seerr_reconciler_models import (
    NormalizedSeerrRequest,
    ReleaseEvidence,
)

SOFT_PATTERNS = {
    "soft_size": ("maximum size", "over max size", "size limit"),
    "soft_quality": ("quality", "cutoff", "preferred", "custom format"),
}
HARD_PATTERNS = {
    "hard_identity": ("wrong series", "wrong movie", "wrong season", "unknown series", "unknown movie"),
    "hard_importability": ("multi-season", "not supported", "unable to parse", "sample", "password"),
    "hard_blocked": ("blocked", "blacklisted", "blocklisted", "dead"),
}


def classify_rejections(rejections: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    classes: list[str] = []
    for rejection in rejections:
        text = rejection.lower()
        matched = False
        for cls, needles in HARD_PATTERNS.items():
            if any(needle in text for needle in needles):
                classes.append(cls)
                matched = True
                break
        if matched:
            continue
        if "unknown custom format" in text or "custom format complaint" in text:
            classes.append("unknown")
            continue
        for cls, needles in SOFT_PATTERNS.items():
            if any(needle in text for needle in needles):
                classes.append(cls)
                matched = True
                break
        if not matched:
            classes.append("unknown")
    return tuple(dict.fromkeys(classes))


def _tokens(text: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", text.lower()) if part}


def _has_season(candidate_title: str, season: int) -> bool:
    title = candidate_title.lower()
    season_number = re.escape(str(season))
    return bool(
        re.search(rf"(?<![a-z0-9])s0?{season_number}(?!\d)", title)
        or re.search(rf"(?<![a-z0-9])season\s+{season_number}(?!\d)", title)
    )


def candidate_identity_confidence(
    request: NormalizedSeerrRequest, candidate: ReleaseEvidence
) -> int:
    req_tokens = _tokens(request.title)
    cand_tokens = _tokens(candidate.title)
    if not req_tokens or not req_tokens.issubset(cand_tokens):
        return 0
    score = 60
    if request.media_type == "series" and request.seasons:
        score += 25 if all(_has_season(candidate.title, s) for s in request.seasons) else -40
    if request.year and str(request.year) in candidate.title:
        score += 10
    if candidate.search_mode.startswith("id:"):
        score += 10
    return max(0, min(score, 100))


def _candidate_hash(candidate: ReleaseEvidence) -> str | None:
    for key in ("hash", "infoHash", "downloadId"):
        value = candidate.raw.get(key)
        if value:
            return str(value).strip().lower()
    return None


def _candidate_usable(
    candidate: ReleaseEvidence,
    failed_guids: set[str],
    failed_hashes: set[str],
) -> tuple[bool, str]:
    if not candidate.guid:
        return False, "candidate missing guid"
    if candidate.guid in failed_guids:
        return False, "candidate already failed"
    candidate_hash = _candidate_hash(candidate)
    normalized_failed_hashes = {
        str(value).strip().lower() for value in failed_hashes
    }
    if candidate_hash and candidate_hash in normalized_failed_hashes:
        return False, "candidate already failed"
    if candidate.indexer_id <= 0:
        return False, "candidate missing indexer id"
    if candidate.size_bytes <= 0:
        return False, "candidate missing size"
    if candidate.seeders <= 0:
        return False, "candidate has no seeders"
    classes = classify_rejections(candidate.rejections)
    if any(cls.startswith("hard_") for cls in classes):
        return False, "hard rejection"
    if "unknown" in classes:
        return False, "unknown rejection"
    return True, "usable"


def fallback_allowed(
    request: NormalizedSeerrRequest,
    candidates: list[ReleaseEvidence],
    failed_guids: set[str],
    failed_hashes: set[str] | None = None,
) -> tuple[bool, str, ReleaseEvidence | None]:
    if not request.stable_ids.has_any():
        return False, "stable external id required", None
    failed_hashes = failed_hashes or set()
    ranked = sorted(
        candidates,
        key=lambda c: (
            -candidate_identity_confidence(request, c),
            -c.seeders,
            -c.size_bytes,
            c.title,
        ),
    )
    blocked_reason = "no candidate"
    for candidate in ranked:
        confidence = candidate_identity_confidence(request, candidate)
        if confidence < 80:
            blocked_reason = "candidate identity confidence too low"
            continue
        usable, reason = _candidate_usable(candidate, failed_guids, failed_hashes)
        if not usable:
            blocked_reason = reason
            continue
        return True, "soft override allowed", candidate
    return False, blocked_reason, None
