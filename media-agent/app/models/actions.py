from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


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
    def _movie_needs_label(self) -> ActionDownloadOptionsMovie:
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
    (
        ActionSearch
        | ActionDownloadOptionsTV
        | ActionDownloadOptionsMovie
        | ActionDownloadGrabTV
        | ActionDownloadGrabMovie
        | ActionIndexerSearch
        | ActionIndexerGrab
    ),
    Field(discriminator="action"),
]


ACTION_CALL_ADAPTER = TypeAdapter(ActionCall)


__all__ = [
    "ActionSearch",
    "ActionDownloadOptionsTV",
    "ActionDownloadOptionsMovie",
    "ActionDownloadGrabTV",
    "ActionDownloadGrabMovie",
    "ActionIndexerSearch",
    "ActionIndexerGrab",
    "ActionCall",
    "ACTION_CALL_ADAPTER",
]
