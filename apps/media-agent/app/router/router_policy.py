from __future__ import annotations

from typing import Final

REUSE_EXISTING_SEASON: Final[str] = "reuseExistingSeason"
LIBRARY_FLOW: Final[str] = "libraryFlow"
INDEXER_FLOW: Final[str] = "indexerFlow"
POST_GRAB_SEASON_FILTER: Final[str] = "postGrabSeasonFilter"

ROUTER_POLICY_TABLE: Final[dict[str, tuple[str, ...]]] = {
    "search": (LIBRARY_FLOW,),
    "download_options_tv": (REUSE_EXISTING_SEASON, LIBRARY_FLOW, INDEXER_FLOW),
    "download_options_movie": (LIBRARY_FLOW, INDEXER_FLOW),
    "download_grab_tv": (LIBRARY_FLOW,),
    "download_grab_movie": (LIBRARY_FLOW,),
    "indexer_search": (INDEXER_FLOW,),
    "indexer_grab": (INDEXER_FLOW, POST_GRAB_SEASON_FILTER),
}

ROUTER_FALLBACK_CODES: Final[frozenset[str]] = frozenset(
    {
        "NO_RELEASES",
        "SERIES_NOT_IN_LIBRARY",
        "MOVIE_NOT_IN_LIBRARY",
        "UNKNOWN_SERIES_ID",
        "UNKNOWN_MOVIE_ID",
    }
)

SESSION_OPTION_SOURCE_ACTIONS: Final[frozenset[str]] = frozenset(
    {"download_options_tv", "download_options_movie", "indexer_search"}
)
