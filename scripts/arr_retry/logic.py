from __future__ import annotations

from dataclasses import dataclass, field


FORCE_GRAB_ALLOWED_FRAGMENTS = (
    "larger than maximum allowed",
    "smaller than minimum allowed",
    "is not wanted in profile",
    "release in queue already meets cutoff",
    "recent grab event in history already meets cutoff",
    "existing file meets cutoff",
)


@dataclass
class RetryPlan:
    search_ids: set[int] = field(default_factory=set)
    force_grab_candidates: dict[int, dict] = field(default_factory=dict)

    def cap(self, max_searches: int) -> None:
        if len(self.search_ids) > max_searches:
            self.search_ids = set(sorted(self.search_ids)[:max_searches])
        if len(self.force_grab_candidates) > max_searches:
            keep_ids = set(sorted(self.force_grab_candidates.keys())[:max_searches])
            self.force_grab_candidates = {
                item_id: release
                for item_id, release in self.force_grab_candidates.items()
                if item_id in keep_ids
            }

    @property
    def total(self) -> int:
        return len(self.search_ids) + len(self.force_grab_candidates)


def queue_item_looks_stalled(item: dict) -> bool:
    if item.get("protocol") != "torrent":
        return False

    text_parts = [
        str(item.get("status", "")),
        str(item.get("trackedDownloadStatus", "")),
        str(item.get("trackedDownloadState", "")),
        str(item.get("errorMessage", "")),
    ]
    for msg in item.get("statusMessages") or []:
        if isinstance(msg, dict):
            text_parts.append(str(msg.get("title", "")))
            text_parts.append(str(msg.get("messages", "")))
        else:
            text_parts.append(str(msg))

    blob = " ".join(text_parts).lower()
    stalled_tokens = ("stalled", "no connections", "no peers", "no seeders", "tracker")
    return any(token in blob for token in stalled_tokens)


def analyze_releases(releases: list[dict], min_seeders: int) -> tuple[bool, bool]:
    has_approved = False
    has_seeded = False
    for release in releases:
        seeders = release.get("seeders") or 0
        if seeders >= min_seeders:
            has_seeded = True
        if release.get("approved") and release.get("downloadAllowed", True):
            has_approved = True
    return has_approved, has_seeded


def is_force_grab_eligible_rejection(reason: str) -> bool:
    text = reason.lower()
    return any(fragment in text for fragment in FORCE_GRAB_ALLOWED_FRAGMENTS)


def choose_force_grab_candidate(releases: list[dict], min_seeders: int) -> dict | None:
    candidates: list[dict] = []

    for release in releases:
        if release.get("approved"):
            continue
        if not release.get("downloadAllowed", True):
            continue

        seeders = release.get("seeders") or 0
        if seeders < min_seeders:
            continue

        rejections = release.get("rejections") or []
        if not rejections:
            continue
        if all(is_force_grab_eligible_rejection(reason) for reason in rejections):
            candidates.append(release)

    if not candidates:
        return None

    return max(candidates, key=lambda release: (release.get("seeders") or 0, -(release.get("size") or 0)))
