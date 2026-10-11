from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
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


# --- download-options / download-grab (indexer results + Sonarr/Radarr grab) ---


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
    def _movie_needs_label(self) -> "DownloadOptionsMovieRequest":
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
    def _ids(self) -> "DownloadGrabRequest":
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


# --- Prowlarr: search indexers without *arr library ---


class IndexerSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=10, ge=1, le=100)
    # Prowlarr SearchResource.Type, e.g. "search", "tvsearch", "moviesearch"
    search_type: str = Field(default="search", min_length=1, max_length=32)


class IndexerGrabRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Full object from a prior indexer-search `options[].release` (Prowlarr cache key = indexerId+guid)
    release: dict[str, Any]


class RouterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=500)
    session_key: str | None = Field(default=None, min_length=1, max_length=200)


class RouterIntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["download", "selection", "non_media"]
    reason: str = Field(min_length=1, max_length=200)


class RouterExtractDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: Literal["tv", "movie"] | None = None
    query: str | None = None
    season: int | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    missing_fields: list[str] = Field(default_factory=list)


class RouterPendingOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1, le=100)
    option_id: str | None = Field(default=None, min_length=4, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    guid: str | None = None
    episode_id: int | None = None
    movie_id: int | None = None
    release: dict[str, Any] | None = None


class RouterSessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_key: str = Field(min_length=1, max_length=200)
    created_at_ms: int
    expires_at_ms: int
    source_action: Literal[
        "download_options_tv", "download_options_movie", "indexer_search"
    ]
    query: str
    media_type: Literal["tv", "movie"] | None = None
    season: int | None = None
    options: list[RouterPendingOption] = Field(default_factory=list)


# --- Strict function/action dispatch layer ---


class ActionSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["search"]
    type: Literal["tv", "movie"]
    query: str = Field(min_length=2, max_length=120)


class ActionDownloadOptionsTV(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["download_options_tv"]
    query: str = Field(min_length=2, max_length=200)
    season: int = Field(ge=0, le=100)
    series_id: int | None = None
    include_full_series_packs: bool = True


class ActionDownloadOptionsMovie(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["download_options_movie"]
    query: str = Field(default="", max_length=200)
    movie_id: int | None = None

    @model_validator(mode="after")
    def _movie_needs_label(self) -> "ActionDownloadOptionsMovie":
        if self.movie_id is None and len((self.query or "").strip()) < 2:
            raise ValueError(
                "download_options_movie: provide query (2+ chars) or movie_id"
            )
        return self


class ActionDownloadGrabTV(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["download_grab_tv"]
    guid: str = Field(min_length=1, max_length=4000)
    episode_id: int


class ActionDownloadGrabMovie(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["download_grab_movie"]
    guid: str = Field(min_length=1, max_length=4000)
    movie_id: int


class ActionIndexerSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["indexer_search"]
    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=10, ge=1, le=100)
    search_type: str = Field(default="search", min_length=1, max_length=32)


class ActionIndexerGrab(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["indexer_grab"]
    release: dict[str, Any]


ActionCall = Annotated[
    ActionSearch | ActionDownloadOptionsTV | ActionDownloadOptionsMovie | ActionDownloadGrabTV | ActionDownloadGrabMovie | ActionIndexerSearch | ActionIndexerGrab,
    Field(discriminator="action"),
]


ACTION_CALL_ADAPTER = TypeAdapter(ActionCall)
