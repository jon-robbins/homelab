from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


__all__ = [
    "RouterRequest",
    "RouterIntentDecision",
    "RouterExtractDecision",
    "RouterPendingOption",
    "RouterSessionState",
]
