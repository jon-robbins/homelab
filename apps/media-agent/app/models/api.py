from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class SearchRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tv", "movie"]
    query: str = Field(min_length=2, max_length=120)

    @field_validator("query")
    @classmethod
    def normalize_whitespace(cls, v: str) -> str:
        s = " ".join(v.split())
        if not s or len(s) < 2:
            raise ValueError("query too short after normalization")
        return s


class ExternalIds(BaseModel):
    model_config = {"extra": "forbid"}

    tvdb: int | None = None
    tmdb: int | None = None
    imdb: str | None = None


class ResultItem(BaseModel):
    model_config = {"extra": "forbid"}

    type: Literal["tv", "movie"]
    title: str
    year: int | None = None
    overview: str
    external_ids: ExternalIds
    in_library: bool


class SearchSuccessResponse(BaseModel):
    model_config = {"extra": "forbid"}

    ok: Literal[True] = True
    type: Literal["tv", "movie"]
    query: str
    normalized_query: str
    request_id: str
    results: list[ResultItem]


class ErrorBody(BaseModel):
    model_config = {"extra": "forbid"}

    code: str
    message: str


class ErrorResponse(BaseModel):
    model_config = {"extra": "forbid"}

    ok: Literal[False] = False
    request_id: str
    error: ErrorBody


class HealthResponse(BaseModel):
    model_config = {"extra": "forbid"}

    ok: bool
    service: str
    sonarr: str
    radarr: str
    prowlarr: str


class DownloadOptionsTVRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tv"] = "tv"
    query: str = Field(min_length=2, max_length=200)
    season: int = Field(ge=0, le=100)
    series_id: int | None = None
    include_full_series_packs: bool = True


class DownloadOptionsMovieRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["movie"] = "movie"
    query: str = Field(default="", max_length=200)
    movie_id: int | None = None

    @model_validator(mode="after")
    def _movie_needs_label(self) -> DownloadOptionsMovieRequest:
        if self.movie_id is None and len((self.query or "").strip()) < 2:
            raise ValueError("movie: provide query (2+ chars) or movie_id")
        return self


class DownloadGrabRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tv", "movie"]
    guid: str = Field(min_length=1, max_length=4000)
    episode_id: int | None = None
    movie_id: int | None = None

    @model_validator(mode="after")
    def _ids(self) -> DownloadGrabRequest:
        if self.type == "tv" and self.episode_id is None:
            raise ValueError("tv grab requires episode_id (from the chosen option row)")
        if self.type == "movie" and self.movie_id is None:
            raise ValueError(
                "movie grab requires movie_id (from the chosen option row)"
            )
        if self.type == "tv" and self.movie_id is not None:
            raise ValueError("tv grab must not set movie_id")
        if self.type == "movie" and self.episode_id is not None:
            raise ValueError("movie grab must not set episode_id")
        return self


class IndexerSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=10, ge=1, le=100)
    search_type: str = Field(default="search", min_length=1, max_length=32)


class IndexerGrabRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release: dict[str, Any]


__all__ = [
    "SearchRequestModel",
    "ExternalIds",
    "ResultItem",
    "SearchSuccessResponse",
    "ErrorBody",
    "ErrorResponse",
    "HealthResponse",
    "DownloadOptionsTVRequest",
    "DownloadOptionsMovieRequest",
    "DownloadGrabRequest",
    "IndexerSearchRequest",
    "IndexerGrabRequest",
]
