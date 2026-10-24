"""Read-only Plex media-library integration for homelab-mcp.

Visibility diagnosis + read-only search. NO mutations.
Wire with @tool (from mcp_schema.make_register_tool) in server.py.

All public tool functions return str — a JSON envelope via:
    from ..envelope import ok_response, error_response
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import httpx

from ..envelope import error_response, ok_response

TOOL_PLEX_SEARCH = "plex_library_search"
TOOL_VISIBILITY = "media_visibility_diagnosis"
TOOL_WATCH_STATUS = "plex_watch_status"

WATCH_PROGRESS_THRESHOLD = 0.05

VisibilityStage = Literal[
    "in_plex_and_arr",
    "in_plex_missing_from_arr",
    "in_arr_missing_from_plex",
    "missing_from_both",
]

READONLY_ERR_HINT_CONFIGURE = (
    "Configure PLEX_TOKEN and PLEX_INTERNAL_URL via Docker secrets / .env to enable."
)
READONLY_ERR_HINT_NETWORK = (
    "Check compose network between homelab-mcp and plex, and Plex HTTP bind address."
)
READONLY_ERR_HINT_AUTH = (
    "Verify PLEX_TOKEN matches the X-Plex-Token expected by your Plex server."
)
READONLY_ERR_HINT_RESPONSE = (
    "Unexpected Plex /search response XML schema. Verify Plex version compatibility."
)


@dataclass(slots=True)
class PlexMatch:
    """A single resolved Plex library item."""
    title: str
    year: int | None
    kind: Literal["movie", "show"]
    file_paths: list[str] | None = None
    rating_key: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "year": self.year,
            "kind": self.kind,
            "file_paths": list(self.file_paths) if self.file_paths is not None else None,
            "rating_key": self.rating_key,
        }


def _sort_matches(matches: list[PlexMatch]) -> list[PlexMatch]:
    def sort_key(m: PlexMatch) -> tuple[int, str]:
        return (-(m.year or 0), m.title.lower())
    return sorted(matches, key=sort_key)


def _parse_year(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class PlexClient:
    """Minimal httpx-backed Plex Web API client. No caching, no mutations.

    Item 12: X-Plex-Token is always sent as HTTP request HEADER, never as
    URL query parameter.
    """

    def __init__(self, base_url: str, token: str | None, *, httpx_client=None) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("plex_internal_url must be a non-empty string")
        self.base_url = base_url.rstrip("/")
        self.token = token or None
        self._httpx = httpx_client

    def is_configured(self) -> bool:
        return bool(self.token)

    def _get_client(self) -> httpx.Client:
        if self._httpx is not None:
            return self._httpx
        return httpx.Client(timeout=15.0)

    def _auth_headers(self) -> dict[str, str] | None:
        if self.token:
            return {"X-Plex-Token": self.token}
        return None

    def is_reachable(self) -> bool:
        """True iff Plex HTTP /identity endpoint responds 200 with <MediaContainer>."""
        if not self.token:
            return False
        url = f"{self.base_url}/identity"
        try:
            client = self._get_client()
            needs_close = self._httpx is None
            try:
                resp = client.get(url, headers=self._auth_headers())
                if resp.status_code != 200:
                    return False
                text = resp.text or ""
                return "<MediaContainer" in text
            finally:
                if needs_close and hasattr(client, "close"):
                    client.close()
        except (httpx.HTTPError, OSError):
            return False

    def _search(
        self,
        title: str,
        year: int | None = None,
        kind: Literal["any", "movie", "show"] = "any",
    ) -> tuple[str, int, list[PlexMatch]]:
        """Perform upstream /search.

        Returns (error_code, status_code, matches) where error_code="" on full
        success and a valid MediaContainer was parsed. If a non-empty error_code
        is returned the caller should produce error_response with it. status_code
        is 0 on transport-level exceptions (timeout / connect error).
        """
        if not self.token:
            return ("UPSTREAM_AUTH", 401, [])
        params: dict[str, Any] = {"query": title}
        if year is not None:
            params["year"] = str(year)
        url = f"{self.base_url}/search?{urlencode(params)}"
        try:
            client = self._get_client()
            needs_close = self._httpx is None
            try:
                resp = client.get(url, headers=self._auth_headers())
            finally:
                if needs_close and hasattr(client, "close"):
                    client.close()
        except httpx.TimeoutException:
            return ("UPSTREAM_TIMEOUT", 0, [])
        except (httpx.HTTPError, OSError):
            return ("UPSTREAM_ERROR", 0, [])

        status = resp.status_code
        if status == 401:
            return ("UPSTREAM_AUTH", status, [])
        if status >= 500 or status == 300 <= status < 400 or status in (403, 404):
            return ("UPSTREAM_ERROR", status, [])
        if status != 200:
            return ("UPSTREAM_ERROR", status, [])

        try:
            root = ET.fromstring(resp.content or b"")
        except ET.ParseError:
            return ("INVALID_RESPONSE", status, [])

        if root.tag != "MediaContainer":
            return ("INVALID_RESPONSE", status, [])

        matches: list[PlexMatch] = []

        for video in root.findall(".//Video"):
            vtype = video.get("type") or ""
            if vtype == "movie":
                kind_k: Literal["movie", "show"] = "movie"
            elif vtype in ("episode", "show"):
                kind_k = "show"
            else:
                continue
            title_val = video.get("title") or ""
            if not title_val and vtype == "episode":
                title_val = video.get("grandparentTitle") or ""
            if not title_val:
                continue
            year_val = _parse_year(video.get("year"))
            rating_key = video.get("ratingKey") or ""
            files: list[str] = []
            for part in video.findall(".//Part"):
                fp = part.get("file")
                if fp:
                    files.append(fp)
            matches.append(
                PlexMatch(
                    title=title_val,
                    year=year_val,
                    kind=kind_k,
                    file_paths=files or None,
                    rating_key=rating_key,
                )
            )

        for directory in root.findall(".//Directory"):
            dtype = directory.get("type") or ""
            if dtype != "show":
                continue
            title_val = directory.get("title") or ""
            if not title_val:
                continue
            year_val = _parse_year(directory.get("year"))
            rating_key = directory.get("ratingKey") or ""
            matches.append(
                PlexMatch(
                    title=title_val,
                    year=year_val,
                    kind="show",
                    file_paths=None,
                    rating_key=rating_key,
                )
            )

        return ("", status, matches)

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[str, int, dict[str, Any] | None]:
        """GET a Plex JSON endpoint. Returns (error_code, status_code, data)."""
        if not self.token:
            return ("UPSTREAM_AUTH", 401, None)
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json", **(self._auth_headers() or {})}
        if extra_headers:
            headers.update(extra_headers)
        try:
            client = self._get_client()
            needs_close = self._httpx is None
            try:
                resp = client.get(url, headers=headers, params=params)
            finally:
                if needs_close and hasattr(client, "close"):
                    client.close()
        except httpx.TimeoutException:
            return ("UPSTREAM_TIMEOUT", 0, None)
        except (httpx.HTTPError, OSError):
            return ("UPSTREAM_ERROR", 0, None)

        status = resp.status_code
        if status == 401:
            return ("UPSTREAM_AUTH", status, None)
        if status >= 500 or status in (403, 404) or 300 <= status < 400:
            return ("UPSTREAM_ERROR", status, None)
        if status != 200:
            return ("UPSTREAM_ERROR", status, None)
        try:
            data = resp.json()
        except ValueError:
            return ("INVALID_RESPONSE", status, None)
        if not isinstance(data, dict):
            return ("INVALID_RESPONSE", status, None)
        return ("", status, data)

    def list_accounts(self) -> tuple[str, int, list[dict[str, Any]]]:
        err, status, data = self._get_json("/accounts")
        if err:
            return (err, status, [])
        accounts = list((data or {}).get("MediaContainer", {}).get("Account", []) or [])
        return ("", status, accounts)

    def get_sessions(self) -> tuple[str, int, list[dict[str, Any]]]:
        err, status, data = self._get_json("/status/sessions")
        if err:
            return (err, status, [])
        sessions = list((data or {}).get("MediaContainer", {}).get("Metadata", []) or [])
        return ("", status, sessions)

    def get_season_episodes(
        self, season_key: str, *, plex_user_id: str
    ) -> tuple[str, int, list[dict[str, Any]]]:
        err, status, data = self._get_json(
            f"/library/metadata/{season_key}/children",
            params={"includeUserState": "1"},
            extra_headers={"X-Plex-User-Id": plex_user_id},
        )
        if err:
            return (err, status, [])
        episodes = list((data or {}).get("MediaContainer", {}).get("Metadata", []) or [])
        return ("", status, episodes)

    def get_show_seasons(self, show_key: str) -> tuple[str, int, list[dict[str, Any]]]:
        err, status, data = self._get_json(f"/library/metadata/{show_key}/children")
        if err:
            return (err, status, [])
        seasons = list((data or {}).get("MediaContainer", {}).get("Metadata", []) or [])
        return ("", status, seasons)

    def get_continue_watching(
        self, *, plex_user_id: str
    ) -> tuple[str, int, list[dict[str, Any]]]:
        err, status, data = self._get_json(
            "/hubs/home/continueWatching",
            extra_headers={"X-Plex-User-Id": plex_user_id},
        )
        if err:
            return (err, status, [])
        items = list((data or {}).get("MediaContainer", {}).get("Metadata", []) or [])
        return ("", status, items)


def _resolve_plex_user(
    accounts: list[dict[str, Any]], username: str
) -> tuple[str, str] | None:
    """Map a username to (plex_user_id, canonical_name)."""
    needle = username.strip().casefold()
    if not needle:
        return None
    exact: tuple[str, str] | None = None
    partial: tuple[str, str] | None = None
    for account in accounts:
        name = (account.get("name") or "").strip()
        if not name:
            continue
        user_id = str(account.get("id", ""))
        if not user_id:
            continue
        folded = name.casefold()
        if folded == needle:
            exact = (user_id, name)
            break
        if partial is None and needle in folded:
            partial = (user_id, name)
    return exact or partial


def _progress_percent(view_offset: int | None, duration: int | None) -> float | None:
    if not view_offset or not duration:
        return None
    return round(100.0 * view_offset / duration, 1)


def _episode_status(episode: dict[str, Any]) -> str:
    view_count = episode.get("viewCount") or 0
    view_offset = episode.get("viewOffset") or 0
    duration = episode.get("duration") or 0
    ratio = (view_offset / duration) if duration else 0.0
    if view_count > 0 and ratio < WATCH_PROGRESS_THRESHOLD:
        return "watched"
    if view_offset and ratio >= WATCH_PROGRESS_THRESHOLD:
        return "in_progress"
    return "unwatched"


def _session_for_user(session: dict[str, Any]) -> dict[str, Any] | None:
    user = session.get("User") or {}
    username = user.get("title") or ""
    if not username:
        return None
    player = session.get("Player") or {}
    payload = _activity_from_metadata(
        session,
        state=player.get("state") or "unknown",
        username=username,
        player=player.get("title") or player.get("product") or "",
        platform=player.get("platform") or "",
    )
    return payload


def _activity_from_metadata(
    item: dict[str, Any],
    *,
    state: str,
    username: str | None = None,
    player: str = "",
    platform: str = "",
) -> dict[str, Any]:
    media_type = item.get("type") or ""
    duration = item.get("duration")
    view_offset = item.get("viewOffset")
    view_count = item.get("viewCount") or 0
    ratio = (view_offset / duration) if view_offset and duration else 0.0
    if state == "stopped" and view_count > 0 and ratio < WATCH_PROGRESS_THRESHOLD:
        watch_status = "watched"
    elif view_offset and ratio >= WATCH_PROGRESS_THRESHOLD:
        watch_status = "in_progress"
    elif view_count > 0:
        watch_status = "watched"
    else:
        watch_status = "unwatched"

    payload: dict[str, Any] = {
        "state": state,
        "watch_status": watch_status,
        "type": media_type,
        "title": item.get("title") or "",
        "progress_percent": _progress_percent(view_offset, duration),
        "view_offset_ms": view_offset,
        "duration_ms": duration,
        "view_count": view_count,
        "last_viewed_at": item.get("lastViewedAt"),
    }
    if username:
        payload["username"] = username
    if player:
        payload["player"] = player
    if platform:
        payload["platform"] = platform
    if media_type == "episode":
        payload["show"] = item.get("grandparentTitle") or ""
        payload["season"] = item.get("parentTitle") or ""
        payload["season_index"] = item.get("parentIndex")
        payload["episode_index"] = item.get("index")
    elif media_type == "movie":
        payload["year"] = item.get("year")
    return payload


def _most_recent_activity(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None
    latest = max(items, key=lambda item: item.get("lastViewedAt") or 0)
    return _activity_from_metadata(latest, state="stopped")


def _show_matches_query(show: dict[str, Any], query: str) -> bool:
    title = (show.get("title") or "").casefold()
    needle = query.strip().casefold()
    return bool(title and needle and needle in title)


def _summarize_show_progress(
    client: PlexClient,
    *,
    show: dict[str, Any],
    plex_user_id: str,
) -> tuple[str, dict[str, Any] | None]:
    show_key = str(show.get("ratingKey") or "")
    if not show_key:
        return ("INVALID_RESPONSE", None)
    err, _, seasons = client.get_show_seasons(show_key)
    if err:
        return (err, None)

    season_summaries: list[dict[str, Any]] = []
    for season in sorted(seasons, key=lambda s: s.get("index") or 0):
        season_key = str(season.get("ratingKey") or "")
        if not season_key:
            continue
        err, _, episodes = client.get_season_episodes(
            season_key, plex_user_id=plex_user_id
        )
        if err:
            return (err, None)
        watched = in_progress = 0
        episode_rows: list[dict[str, Any]] = []
        for episode in sorted(episodes, key=lambda e: e.get("index") or 0):
            status = _episode_status(episode)
            if status == "watched":
                watched += 1
            elif status == "in_progress":
                in_progress += 1
            if status == "unwatched":
                continue
            row: dict[str, Any] = {
                "episode": episode.get("index"),
                "title": episode.get("title") or "",
                "status": status,
            }
            if status == "in_progress":
                row["progress_percent"] = _progress_percent(
                    episode.get("viewOffset"), episode.get("duration")
                )
            episode_rows.append(row)
        if not episode_rows:
            continue
        season_summaries.append(
            {
                "season": season.get("index"),
                "title": season.get("title") or "",
                "watched": watched,
                "in_progress": in_progress,
                "total": len(episodes),
                "episodes": episode_rows,
            }
        )

    return (
        "",
        {
            "show": show.get("title") or "",
            "rating_key": show_key,
            "seasons": season_summaries,
        },
    )


def _plex_upstream_error(tool: str, err_code: str) -> str:
    if err_code == "UPSTREAM_TIMEOUT":
        return error_response(
            tool,
            code="UPSTREAM_TIMEOUT",
            message="Plex request timed out.",
            hint=READONLY_ERR_HINT_NETWORK,
        )
    if err_code == "UPSTREAM_AUTH":
        return error_response(
            tool,
            code="UPSTREAM_AUTH",
            message="Plex rejected the request with HTTP 401 (bad X-Plex-Token).",
            hint=READONLY_ERR_HINT_AUTH,
        )
    if err_code == "INVALID_RESPONSE":
        return error_response(
            tool,
            code="INVALID_RESPONSE",
            message="Plex returned unparseable or unexpected JSON.",
            hint=READONLY_ERR_HINT_RESPONSE,
        )
    return error_response(
        tool,
        code="UPSTREAM_ERROR",
        message=f"Plex request failed with error code '{err_code}'.",
        hint=READONLY_ERR_HINT_NETWORK,
    )


def plex_watch_status(
    username: str,
    show: str | None = None,
    *,
    _settings=None,
    _httpx_client=None,
) -> str:
    """Report what a Plex user is watching now and optional show progress.

    Returns envelope JSON with active_session, recent_activity, and show_progress.
    """
    started = time.perf_counter()
    if _settings is None or _settings.plex_token is None:
        return error_response(
            TOOL_WATCH_STATUS,
            code="UPSTREAM_AUTH",
            message="PLEX_TOKEN is not configured on this homelab-mcp instance.",
            hint=READONLY_ERR_HINT_CONFIGURE,
        )
    username = (username or "").strip()
    if not username:
        return error_response(
            TOOL_WATCH_STATUS,
            code="INVALID_ARGUMENT",
            message="username is required (Plex display name, e.g. 'Quinn Cartwright').",
        )
    try:
        client = PlexClient(
            base_url=_settings.plex_internal_url,
            token=_settings.plex_token,
            httpx_client=_httpx_client,
        )
    except ValueError as exc:
        return error_response(
            TOOL_WATCH_STATUS,
            code="UPSTREAM_ERROR",
            message=f"Invalid Plex client configuration: {exc}",
            hint=READONLY_ERR_HINT_CONFIGURE,
        )

    err, _, accounts = client.list_accounts()
    if err:
        return _plex_upstream_error(TOOL_WATCH_STATUS, err)
    resolved = _resolve_plex_user(accounts, username)
    if resolved is None:
        return error_response(
            TOOL_WATCH_STATUS,
            code="NOT_FOUND",
            message=f"No Plex user matched username '{username}'.",
            hint="Use the exact Plex display name from Settings > Users.",
        )
    plex_user_id, canonical_name = resolved

    err, _, sessions = client.get_sessions()
    if err:
        return _plex_upstream_error(TOOL_WATCH_STATUS, err)
    active_session: dict[str, Any] | None = None
    for session in sessions:
        session_user = (session.get("User") or {}).get("title") or ""
        if session_user.casefold() == canonical_name.casefold():
            active_session = _session_for_user(session)
            break

    recent_activity: dict[str, Any] | None = None
    if active_session is None:
        err, _, continue_items = client.get_continue_watching(plex_user_id=plex_user_id)
        if err:
            return _plex_upstream_error(TOOL_WATCH_STATUS, err)
        recent_activity = _most_recent_activity(continue_items)

    show_progress: dict[str, Any] | None = None
    if show and show.strip():
        err, _, search_data = client._get_json(
            "/search", params={"query": show.strip(), "limit": "25"}
        )
        if err:
            return _plex_upstream_error(TOOL_WATCH_STATUS, err)
        candidates = list(
            (search_data or {}).get("MediaContainer", {}).get("Metadata", []) or []
        )
        matched_shows = [
            item
            for item in candidates
            if item.get("type") == "show" and _show_matches_query(item, show)
        ]
        if not matched_shows:
            return ok_response(
                TOOL_WATCH_STATUS,
                {
                    "code": "NO_MATCH",
                    "username": canonical_name,
                    "plex_user_id": plex_user_id,
                    "active_session": active_session,
                    "recent_activity": recent_activity,
                    "show_progress": None,
                    "notes": [f"no_show_match_for={show.strip()!r}"],
                },
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        err, show_progress = _summarize_show_progress(
            client, show=matched_shows[0], plex_user_id=plex_user_id
        )
        if err:
            return _plex_upstream_error(TOOL_WATCH_STATUS, err)

    data_code = "OK"
    if active_session is None and recent_activity is None and show_progress is None:
        data_code = "NO_ACTIVITY"
    duration_ms = int((time.perf_counter() - started) * 1000)
    return ok_response(
        TOOL_WATCH_STATUS,
        {
            "code": data_code,
            "username": canonical_name,
            "plex_user_id": plex_user_id,
            "active_session": active_session,
            "recent_activity": recent_activity,
            "show_progress": show_progress,
        },
        duration_ms=duration_ms,
    )


def plex_library_search(
    title: str,
    year: int | None = None,
    kind: Literal["any", "movie", "show"] = "any",
    *,
    _settings=None,
    _httpx_client=None,
) -> str:
    """Search the local Plex library for a title+year+kind match.

    Returns envelope JSON str:
      ok:  {"ok":True, "data":{"matches":[...], "code":"OK"|"NO_MATCH"}, ...}
      err: {"ok":False, "error":{"code":"UPSTREAM_TIMEOUT|UPSTREAM_AUTH|UPSTREAM_ERROR|INVALID_RESPONSE",...}, ...}
    """
    started = time.perf_counter()
    if _settings is None or _settings.plex_token is None:
        return error_response(
            TOOL_PLEX_SEARCH,
            code="UPSTREAM_AUTH",
            message="PLEX_TOKEN is not configured on this homelab-mcp instance.",
            hint=READONLY_ERR_HINT_CONFIGURE,
        )
    try:
        client = PlexClient(
            base_url=_settings.plex_internal_url,
            token=_settings.plex_token,
            httpx_client=_httpx_client,
        )
    except ValueError as exc:
        return error_response(
            TOOL_PLEX_SEARCH,
            code="UPSTREAM_ERROR",
            message=f"Invalid Plex client configuration: {exc}",
            hint=READONLY_ERR_HINT_CONFIGURE,
        )

    err_code, _status, matches = client._search(title=title, year=year, kind=kind)
    if err_code:
        if err_code == "UPSTREAM_TIMEOUT":
            return error_response(
                TOOL_PLEX_SEARCH,
                code="UPSTREAM_TIMEOUT",
                message="Plex /search request timed out.",
                hint=READONLY_ERR_HINT_NETWORK,
            )
        if err_code == "UPSTREAM_AUTH":
            return error_response(
                TOOL_PLEX_SEARCH,
                code="UPSTREAM_AUTH",
                message="Plex rejected the request with HTTP 401 (bad X-Plex-Token).",
                hint=READONLY_ERR_HINT_AUTH,
            )
        if err_code == "INVALID_RESPONSE":
            return error_response(
                TOOL_PLEX_SEARCH,
                code="INVALID_RESPONSE",
                message="Plex /search returned unparseable or unexpected XML.",
                hint=READONLY_ERR_HINT_RESPONSE,
            )
        return error_response(
            TOOL_PLEX_SEARCH,
            code="UPSTREAM_ERROR",
            message=f"Plex /search failed with error code '{err_code}'.",
            hint=READONLY_ERR_HINT_NETWORK,
        )

    if kind != "any":
        matches = [m for m in matches if m.kind == kind]
    if year is not None:
        matches = [m for m in matches if m.year == year]
    matches = _sort_matches(matches)

    data_code = "OK" if matches else "NO_MATCH"
    duration_ms = int((time.perf_counter() - started) * 1000)
    return ok_response(
        TOOL_PLEX_SEARCH,
        {"matches": [m.to_dict() for m in matches], "code": data_code},
        duration_ms=duration_ms,
    )


def media_visibility_diagnosis(
    title: str,
    year: int | None = None,
    kind: Literal["any", "movie", "show"] = "any",
    *,
    _settings=None,
    _arr_fn_lookup: Callable[[str, str, str], tuple[str, list[dict]]] | None = None,
    _httpx_client=None,
) -> str:
    """Determine visibility stage for a title across Plex + ARRs.

    Returns envelope JSON str with data containing:
        {"stage": VisibilityStage,
         "in_plex": list[PlexMatch.to_dict()],
         "in_sonarr": list[dict] | absent,
         "in_radarr": list[dict] | absent,
         "notes": list[str],
         "code": "OK"}
    Or on upstream Plex/Arr connection failure:
        error_response(TOOL_VISIBILITY, code=..., message=..., hint=...)

    Item 9: If no _arr_fn_lookup injected, calls the real media.arr_lookup()
    infrastructure via tools.media module, then parses the returned envelope
    JSON str for actual arr candidates. An Arr item is considered present only
    when candidate.in_library == True (preserving has_file).
    If Plex search fails with auth/timeout/network/invalid-response errors,
    an indeterminate upstream error envelope is returned instead of treating
    it as Plex missing.
    """
    started = time.perf_counter()
    notes: list[str] = []
    plex_configured = bool(_settings is not None and _settings.plex_token is not None)
    notes.append(f"plex_token_configured={plex_configured}")

    # --- 1. Plex search ---
    plex_matches_list_of_dict: list[dict] = []
    if not plex_configured:
        return error_response(
            TOOL_VISIBILITY,
            code="UPSTREAM_AUTH",
            message="PLEX_TOKEN is not configured on this homelab-mcp instance.",
            hint=READONLY_ERR_HINT_CONFIGURE,
        )

    plex_env_raw = plex_library_search(
        title=title,
        year=year,
        kind=kind,
        _settings=_settings,
        _httpx_client=_httpx_client,
    )
    try:
        plex_env = json.loads(plex_env_raw)
    except json.JSONDecodeError:
        return error_response(
            TOOL_VISIBILITY,
            code="INVALID_RESPONSE",
            message="Plex /search returned unparseable JSON envelope.",
            hint=READONLY_ERR_HINT_RESPONSE,
        )

    if plex_env.get("ok") is not True:
        plex_err = plex_env.get("error", {}) if isinstance(plex_env, dict) else {}
        err_code = plex_err.get("code", "UPSTREAM_ERROR")
        err_msg = plex_err.get("message", f"Plex library search failed: {err_code}")
        err_hint = plex_err.get("hint", READONLY_ERR_HINT_NETWORK)
        return error_response(
            TOOL_VISIBILITY,
            code=err_code,
            message=err_msg,
            hint=err_hint,
        )

    plex_matches_list_of_dict = list(
        plex_env.get("data", {}).get("matches", []) or []
    )
    in_plex = bool(plex_matches_list_of_dict)
    notes.append(f"plex_matches={len(plex_matches_list_of_dict)}")

    # --- 2. Arr lookups ---
    sonarr_hits_dicts_or_absent: list[dict] | None = None
    radarr_hits_dicts_or_absent: list[dict] | None = None

    show_like = kind in {"show", "any"}
    movie_like = kind in {"movie", "any"}

    def _do_arr_call(media_type: str, arr_kind_label: str) -> tuple[str, list[dict], dict[str, str] | None]:
        """Return (kind_str, hits_list, error_dict). Returns error_dict on failure."""
        if _arr_fn_lookup is not None:
            year_str = "" if year is None else str(year)
            try:
                raw_kind, raw_hits = _arr_fn_lookup(arr_kind_label, title, year_str)
                # Filter to only in_library == True items
                library_hits = [
                    h for h in raw_hits
                    if isinstance(h, dict) and h.get("in_library") is True
                ]
                return (raw_kind, library_hits, None)
            except Exception as exc:
                return (arr_kind_label, [], {"code": "UPSTREAM_ERROR", "message": f"{arr_kind_label} lookup failed: {exc}", "hint": READONLY_ERR_HINT_NETWORK})
        if _settings is None:
            return (arr_kind_label, [], {"code": "UPSTREAM_ERROR", "message": "Settings not configured", "hint": READONLY_ERR_HINT_CONFIGURE})
        try:
            from homelab_mcp.tools import media as _media_mod
        except Exception as exc:
            return (arr_kind_label, [], {"code": "UPSTREAM_ERROR", "message": f"Could not import media tools: {exc}", "hint": READONLY_ERR_HINT_NETWORK})
        try:
            env_str = _media_mod.arr_lookup(
                _settings, media_type=media_type, query=title
            )
        except Exception as exc:
            return (arr_kind_label, [], {"code": "UPSTREAM_ERROR", "message": f"{arr_kind_label} request exception: {exc}", "hint": READONLY_ERR_HINT_NETWORK})
        try:
            env = json.loads(env_str)
        except json.JSONDecodeError:
            return (arr_kind_label, [], {"code": "INVALID_RESPONSE", "message": f"{arr_kind_label} returned malformed JSON", "hint": READONLY_ERR_HINT_RESPONSE})
        if env.get("ok") is not True:
            err = env.get("error", {}) if isinstance(env, dict) else {}
            code = err.get("code", "UPSTREAM_ERROR")
            if code == "NOT_FOUND":
                # Clean zero matches is not an error
                return (arr_kind_label, [], None)
            return (arr_kind_label, [], {
                "code": code,
                "message": err.get("message", f"{arr_kind_label} lookup failed: {code}"),
                "hint": err.get("hint", READONLY_ERR_HINT_NETWORK),
            })
        candidates = list(env.get("data", {}).get("candidates", []) or [])
        filtered: list[dict] = []
        for h in candidates:
            if not isinstance(h, dict):
                continue
            # Treat an Arr item as present only when candidate.in_library == True
            if h.get("in_library") is not True:
                continue
            if year is not None:
                hy = h.get("year")
                if isinstance(hy, int) and hy != year:
                    continue
            filtered.append(h)
        return (arr_kind_label, filtered, None)

    if show_like:
        _, hits, err = _do_arr_call("series", "show")
        if err is not None:
            return error_response(
                TOOL_VISIBILITY,
                code=err["code"],
                message=err["message"],
                hint=err["hint"],
            )
        sonarr_hits_dicts_or_absent = hits
        notes.append(f"sonarr_matches={len(hits)}")
    if movie_like:
        _, hits, err = _do_arr_call("movie", "movie")
        if err is not None:
            return error_response(
                TOOL_VISIBILITY,
                code=err["code"],
                message=err["message"],
                hint=err["hint"],
            )
        radarr_hits_dicts_or_absent = hits
        notes.append(f"radarr_matches={len(hits)}")

    effective_arr: list[dict] = []
    if kind == "movie":
        effective_arr = list(radarr_hits_dicts_or_absent or [])
    elif kind == "show":
        effective_arr = list(sonarr_hits_dicts_or_absent or [])
    else:
        effective_arr = list(sonarr_hits_dicts_or_absent or []) + list(
            radarr_hits_dicts_or_absent or []
        )
    in_arr = bool(effective_arr)

    # --- 3. Visibility stage truth table ---
    if in_plex and in_arr:
        stage: VisibilityStage = "in_plex_and_arr"
    elif in_plex and not in_arr:
        stage = "in_plex_missing_from_arr"
    elif not in_plex and in_arr:
        stage = "in_arr_missing_from_plex"
    else:
        stage = "missing_from_both"

    payload: dict[str, Any] = {
        "stage": stage,
        "in_plex": plex_matches_list_of_dict,
        "notes": notes,
        "code": "OK",
    }
    if sonarr_hits_dicts_or_absent is not None:
        payload["in_sonarr"] = sonarr_hits_dicts_or_absent
    if radarr_hits_dicts_or_absent is not None:
        payload["in_radarr"] = radarr_hits_dicts_or_absent

    duration_ms = int((time.perf_counter() - started) * 1000)
    return ok_response(TOOL_VISIBILITY, payload, duration_ms=duration_ms)
