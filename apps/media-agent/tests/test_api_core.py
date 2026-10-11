"""Core health/search/download endpoint coverage."""

from test_api import (
    test_download_grab_tv,
    test_download_options_tv_merges_releases,
    test_health,
    test_search_tv_success,
    test_search_unauthorized,
    test_search_validation_extra_field,
)

__all__ = [
    "test_search_tv_success",
    "test_search_unauthorized",
    "test_search_validation_extra_field",
    "test_health",
    "test_download_options_tv_merges_releases",
    "test_download_grab_tv",
]
