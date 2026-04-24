"""Indexer + action/function endpoint coverage."""

from test_api import (
    test_action_dispatch_search_success,
    test_action_dispatch_validation_error,
    test_functions_list_requires_auth,
    test_functions_list_success,
    test_indexer_grab_prowlarr,
    test_indexer_search_prowlarr,
)

__all__ = [
    "test_indexer_search_prowlarr",
    "test_indexer_grab_prowlarr",
    "test_functions_list_requires_auth",
    "test_functions_list_success",
    "test_action_dispatch_search_success",
    "test_action_dispatch_validation_error",
]
