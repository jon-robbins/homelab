from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MediaType = Literal["movie", "series"]
RequestState = Literal[
    "observed_request",
    "waiting_for_arr",
    "arr_monitored",
    "searching_arr",
    "arr_downloading",
    "fallback_candidate_found",
    "fallback_grabbed",
    "downloading",
    "imported",
    "available",
    "blocked_no_candidate",
    "blocked_identity_ambiguous",
    "blocked_hard_rejection",
    "blocked_no_seeders",
    "blocked_import_failed",
    "failed_api_error",
]


@dataclass(frozen=True)
class StableIds:
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None

    def has_any(self) -> bool:
        return self.tmdb_id is not None or self.tvdb_id is not None or bool(self.imdb_id)

    def as_dict(self) -> dict[str, Any]:
        return {"tmdb_id": self.tmdb_id, "tvdb_id": self.tvdb_id, "imdb_id": self.imdb_id}


@dataclass(frozen=True)
class NormalizedSeerrRequest:
    request_id: int
    media_type: MediaType
    title: str
    year: int | None
    seasons: tuple[int, ...]
    stable_ids: StableIds
    arr_id: int | None
    status: str
    requested_at: str | None = None

    @property
    def state_key(self) -> str:
        season_part = ",".join(str(season) for season in self.seasons) or "movie"
        return f"{self.request_id}:{self.media_type}:{season_part}"


@dataclass(frozen=True)
class ReleaseEvidence:
    title: str
    guid: str
    indexer_id: int
    size_bytes: int
    seeders: int
    leechers: int = 0
    quality: str | None = None
    rejections: tuple[str, ...] = ()
    download_allowed: bool = False
    search_mode: str = "title"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiagnosisResult:
    request: NormalizedSeerrRequest
    state: RequestState
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    candidates: tuple[ReleaseEvidence, ...] = ()
    recommended_action: str = "wait"
    expected_action: str | None = None
