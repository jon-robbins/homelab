"""Router dispatch, parsing, and fallback coverage."""

from test_api import (
    test_router_dispatch_fallback_persists_indexer_options_for_selection,
    test_router_dispatch_fallback_to_indexer_search,
    test_router_dispatch_parser_invalid,
    test_router_dispatch_search_success,
    test_router_missing_season_number,
    test_router_non_media_intent,
    test_router_title_request_uses_indexer_first,
)

__all__ = [
    "test_router_dispatch_search_success",
    "test_router_dispatch_parser_invalid",
    "test_router_dispatch_fallback_to_indexer_search",
    "test_router_dispatch_fallback_persists_indexer_options_for_selection",
    "test_router_title_request_uses_indexer_first",
    "test_router_non_media_intent",
    "test_router_missing_season_number",
]
