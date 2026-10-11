"""MCP tool metadata: envelope docs, parameter Field annotations, registration helper."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from pydantic import Field

# --- HOMELAB_MCP_ALLOWED_TOOLS 3-way fail-closed semantics ---
# Semantics:
#   env unset       => ALLOWED_TOOLS_SET meaningless, unrestricted mode (register all).
#   env present ""  => ALLOWED_TOOLS_SET empty frozenset, fail closed (skip EVERY tool).
#   env present CSV => ALLOWED_TOOLS_SET = trimmed split, allowlist only.
import os as _os

from homelab_mcp.capabilities import allowed_tools, capability_for

def _configured_allowed_tools() -> tuple[bool, frozenset[str]]:
    """Read the allowlist at registration time so independent servers do not leak state."""
    present = "HOMELAB_MCP_ALLOWED_TOOLS" in _os.environ
    raw = _os.environ.get("HOMELAB_MCP_ALLOWED_TOOLS", "").strip()
    names = frozenset(part.strip() for part in raw.split(",") if part.strip())
    return present, names

ENVELOPE_DOC = """\
Returns a JSON string envelope:
- ok (bool): whether the call succeeded
- tool (str): tool name echo
- data (object): payload described in Output data
- meta.duration_ms (int): server-side timing in milliseconds
- meta.generated_at (str): ISO-8601 UTC timestamp

On error: ok=false, error.code (e.g. UPSTREAM_TIMEOUT, NOT_FOUND, INVALID_ARGUMENT), \
error.message, error.hint."""


def tool_doc(summary: str, output: str) -> str:
    """Build a multi-line MCP tool description for Cursor and other clients."""
    return f"{summary}\n\nOutput data:\n{output}\n\n{ENVELOPE_DOC}"


ServicesFilter = Annotated[
    list[str] | None,
    Field(
        description=(
            "Optional list of compose catalog service names to probe "
            "(e.g. qbittorrent, autobrr, sonarr, radarr). "
            "Omit or null to probe all catalog services."
        ),
    ),
]

TagFilter = Annotated[
    str | None,
    Field(
        description=(
            "qBittorrent tag filter. "
            "Omit or null for all tags."
        ),
    ),
]

CategoryFilter = Annotated[
    str | None,
    Field(description="qBittorrent category filter. Omit or null for all categories."),
]

TorrentStateFilter = Annotated[
    str | None,
    Field(
        description=(
            "qBittorrent state filter passed to the API (e.g. downloading, seeding, paused). "
            "Omit or null for all states."
        ),
    ),
]

TorrentListLimit = Annotated[
    int,
    Field(description="Maximum number of torrent rows to return.", ge=1, le=100),
]

TorrentListOffset = Annotated[
    int,
    Field(description="Number of torrent rows to skip for pagination.", ge=0),
]

HashPrefix = Annotated[
    str,
    Field(
        description="Leading hex characters of the torrent info hash (minimum 8).",
        min_length=8,
    ),
]

BazarrScanLimit = Annotated[
    int,
    Field(description="Maximum number of series/movies to scan for missing subtitles.", ge=1, le=5000),
]

ConfirmFlag = Annotated[
    bool,
    Field(description="Must be true for tier-2 recoverable mutations."),
]

ExpectedAction = Annotated[
    str | None,
    Field(
        description=(
            "Canonical action string for tier-1 mutations. "
            "Must match the value shown in error.hint when a gate fails."
        ),
    ),
]

ResumeTag = Annotated[
    str | None,
    Field(
        description=(
            "Resume torrents with this tag. "
            "Omit when using hashes."
        ),
    ),
]

TorrentHashes = Annotated[
    list[str] | None,
    Field(description="Resume specific torrents by info-hash. Omit when using tag."),
]

OffloadLimit = Annotated[
    int,
    Field(description="Maximum number of offload candidates to return.", ge=1, le=50),
]

QbtProfileName = Annotated[
    str,
    Field(description="qBittorrent preference profile name."),
]

PauseTag = Annotated[
    str | None,
    Field(
        description=(
            "Pause torrents with this tag. "
            "Omit when using hashes."
        ),
    ),
]

MediaSearchQuery = Annotated[
    str,
    Field(description="Title search string for Radarr/Sonarr library or Prowlarr indexer search."),
]

MediaSearchQueryOptional = Annotated[
    str | None,
    Field(
        description=(
            "Optional title search string when series_id is omitted "
            "(Sonarr library resolve)."
        ),
    ),
]

MovieIdFilter = Annotated[
    int | None,
    Field(description="Radarr library movie id. Omit to resolve by query."),
]

SeriesIdFilter = Annotated[
    int | None,
    Field(description="Sonarr library series id. Omit to resolve by query."),
]

SeasonNumber = Annotated[
    int,
    Field(description="Season number for Sonarr release search (0 for series packs when supported).", ge=0),
]

IncludeSeriesPacks = Annotated[
    bool,
    Field(description="Include full-series packs in Sonarr release search."),
]

ProwlarrSearchType = Annotated[
    str,
    Field(description='Prowlarr search type (default "search").'),
]

OverseerrLookbackHours = Annotated[
    int,
    Field(description="Hours of recent Overseerr requests to inspect.", ge=1, le=720),
]

OverseerrRequestLimit = Annotated[
    int,
    Field(description="Maximum number of Overseerr request rows to inspect.", ge=1, le=200),
]

SeerrRequestId = Annotated[
    int | None,
    Field(description="Optional Seerr/Overseerr request id to diagnose."),
]

SeerrDiagnosisTitle = Annotated[
    str | None,
    Field(description="Optional title substring to filter recent Seerr requests."),
]

SeerrDiagnosisLookbackHours = Annotated[
    int,
    Field(description="Hours of recent Seerr requests to inspect.", ge=1, le=720),
]

ReleaseSearchLimit = Annotated[
    int,
    Field(description="Maximum ranked release options to return.", ge=1, le=25),
]

IndexerIdsFilter = Annotated[
    list[int] | None,
    Field(description="Optional Prowlarr indexer id filter."),
]

GrabGuid = Annotated[str, Field(description="Release guid from a prior search tool result.")]

GrabSizeBytes = Annotated[int, Field(description="Release size_bytes from the chosen search option.", ge=1)]

GrabMovieId = Annotated[int, Field(description="Radarr library movie id from search result.")]

GrabEpisodeId = Annotated[int, Field(description="Sonarr episode id from search result.")]

GrabIndexerId = Annotated[int, Field(description="Prowlarr indexer id from search result.")]

GrabTitle = Annotated[
    str | None,
    Field(description="Optional release title for audit logs and Prowlarr grab body."),
]

TorrentHashSingle = Annotated[
    str | None,
    Field(description="qBittorrent info-hash for a single torrent (preferred over name_contains)."),
]

TorrentNameContains = Annotated[
    str | None,
    Field(description="Case-insensitive substring match against torrent name when hash is unknown."),
]

NameContains = Annotated[
    str,
    Field(description="Case-insensitive substring match against torrent names."),
]

MediaType = Annotated[str, Field(description='Arr media type: "movie" or "series".')]

ArrQuery = Annotated[str, Field(description="Title or id term (imdb:tt… / tmdb:… / tvdb:…).")]

TmdbId = Annotated[int | None, Field(description="TMDB id for ensure/lookup.")]

TvdbId = Annotated[int | None, Field(description="TVDB id for series ensure.")]

LibraryId = Annotated[int | None, Field(description="Existing Radarr/Sonarr library id.")]

ArrSeason = Annotated[int | None, Field(description="Season number (required for series ensure).", ge=0)]

MediaStageScope = Annotated[
    str | None,
    Field(description="Optional staging scope: movie, series, season, or episode."),
]

EpisodeNumber = Annotated[int | None, Field(description="Episode number for episode scope.", ge=1)]

QueueId = Annotated[int, Field(description="Arr queue item id.", ge=1)]

F = TypeVar("F", bound=Callable[..., Any])


def make_register_tool(
    mcp: Any,
    profile: str | None = None,
    enable_mutations: bool = True,
    extension_roles: dict[str, tuple[str, str]] | None = None,
) -> Callable[[str, str], Callable[[F], F]]:
    """Return a decorator factory that registers tools with explicit MCP descriptions."""

    def register_tool(summary: str, output: str) -> Callable[[F], F]:
        description = tool_doc(summary, output)

        def decorator(fn: F) -> F:
            fn.__doc__ = description
            tool_name = fn.__name__
            effective_profile = profile or _os.environ.get("HOMELAB_MCP_TOOL_PROFILE")
            if effective_profile:
                if tool_name not in allowed_tools(effective_profile, {tool_name}, extension_roles):
                    return fn
            allowlist_present, allowlist = _configured_allowed_tools()
            if allowlist_present and tool_name not in allowlist:
                return fn
            if not enable_mutations:
                try:
                    if capability_for(tool_name, extension_roles) != "read":
                        return fn
                except KeyError:
                    return fn
            mcp.add_tool(fn, description=description)
            return fn

        return decorator

    return register_tool
