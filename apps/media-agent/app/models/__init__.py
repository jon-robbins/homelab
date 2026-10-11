"""Pydantic schemas used across the media-agent service.

The flat ``app/models.py`` module has been split into
``api``, ``actions``, and ``router`` sub-modules. This package preserves the
original flat public surface so all existing imports keep working.
"""

from .actions import (
    ACTION_CALL_ADAPTER,
    ActionCall,
    ActionDownloadGrabMovie,
    ActionDownloadGrabTV,
    ActionDownloadOptionsMovie,
    ActionDownloadOptionsTV,
    ActionIndexerGrab,
    ActionIndexerSearch,
    ActionSearch,
)
from .api import (
    DownloadGrabRequest,
    DownloadOptionsMovieRequest,
    DownloadOptionsTVRequest,
    ErrorBody,
    ErrorResponse,
    ExternalIds,
    HealthResponse,
    IndexerGrabRequest,
    IndexerSearchRequest,
    ResultItem,
    SearchRequestModel,
    SearchSuccessResponse,
)
from .router import (
    RouterExtractDecision,
    RouterIntentDecision,
    RouterPendingOption,
    RouterRequest,
    RouterSessionState,
)

__all__ = [
    "ACTION_CALL_ADAPTER",
    "ActionCall",
    "ActionDownloadGrabMovie",
    "ActionDownloadGrabTV",
    "ActionDownloadOptionsMovie",
    "ActionDownloadOptionsTV",
    "ActionIndexerGrab",
    "ActionIndexerSearch",
    "ActionSearch",
    "DownloadGrabRequest",
    "DownloadOptionsMovieRequest",
    "DownloadOptionsTVRequest",
    "ErrorBody",
    "ErrorResponse",
    "ExternalIds",
    "HealthResponse",
    "IndexerGrabRequest",
    "IndexerSearchRequest",
    "ResultItem",
    "RouterExtractDecision",
    "RouterIntentDecision",
    "RouterPendingOption",
    "RouterRequest",
    "RouterSessionState",
    "SearchRequestModel",
    "SearchSuccessResponse",
]
