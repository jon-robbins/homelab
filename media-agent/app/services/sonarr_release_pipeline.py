"""TV download-options + grab pipeline running on top of Sonarr /release cache."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import Settings
from ..integrations.sonarr import sonarr_get as _sonarr_get
from ..integrations.sonarr import sonarr_post_json as _sonarr_post_json
from .release_formatting import fold_for_match as _fold
from .release_formatting import human_size
from .release_formatting import indexer_name as _indexer_name
from .release_formatting import int_field as _int_field


@dataclass
class TVContext:
    series_id: int
    series_title: str
    season_number: int
    series_type: str


def resolve_series_in_library(
    client: httpx.Client, s: Settings, q: str, explicit_id: int | None
) -> tuple[TVContext | None, list[dict[str, Any]] | None, str | None]:
    """Return (context, ambiguous_candidates, error_code)."""
    if explicit_id is not None:
        sdt = _sonarr_get(client, s, f"api/v3/series/{explicit_id}", None)
        if sdt.status_code == 404:
            return None, None, "UNKNOWN_SERIES_ID"
        sdt.raise_for_status()
        sdj = sdt.json() if sdt.is_success else None
        if not isinstance(sdj, dict) or _int_field(sdj.get("id")) is None:
            return None, None, "UPSTREAM_BAD_RESPONSE"
        st = str(sdj.get("title") or sdj.get("sortTitle") or "Unknown")
        stype = str(sdj.get("seriesType") or "standard")
        return (
            TVContext(
                series_id=explicit_id,
                series_title=st,
                season_number=0,
                series_type=stype,
            ),
            None,
            None,
        )

    p = f"{q.strip()} S01E01"
    r = _sonarr_get(client, s, "api/v3/parse", {"title": p})
    r.raise_for_status()
    parsed = r.json() if r.is_success else None
    if isinstance(parsed, dict) and (parsed.get("episodes") or []):
        ser = parsed.get("series")
        if isinstance(ser, dict) and _int_field(ser.get("id")) is not None:
            sid = int(ser["id"])
            st = str(ser.get("title") or ser.get("sortTitle") or "Unknown")
            sdt = _sonarr_get(client, s, f"api/v3/series/{sid}", None)
            sdt.raise_for_status()
            sdj = sdt.json() if sdt.is_success else None
            stype = str(
                (sdj.get("seriesType") if isinstance(sdj, dict) else None) or "standard"
            )
            return (TVContext(sid, st, 0, stype), None, None)

    r2 = _sonarr_get(client, s, "api/v3/series", None)
    r2.raise_for_status()
    f = _fold(q)
    cands: list[dict[str, Any]] = []
    for row in r2.json() or []:
        if not isinstance(row, dict):
            continue
        tid = _int_field(row.get("id"))
        if tid is None:
            continue
        t2 = str(row.get("title") or "")
        s2 = str(row.get("sortTitle") or "")
        if f and (f in _fold(t2) or f in _fold(s2) or _fold(t2) in f):
            cands.append(
                {
                    "id": tid,
                    "title": t2,
                    "year": row.get("year"),
                }
            )
    if not cands:
        return None, None, "SERIES_NOT_IN_LIBRARY"
    if len(cands) == 1:
        row = cands[0]
        sdt = _sonarr_get(client, s, f"api/v3/series/{row['id']}", None)
        sdt.raise_for_status()
        sdt2 = sdt.json() if sdt.is_success else {}
        stype = (
            str(sdt2.get("seriesType") or "standard")
            if isinstance(sdt2, dict)
            else "standard"
        )
        return (
            TVContext(
                series_id=int(row["id"]),
                series_title=str(row["title"]),
                season_number=0,
                series_type=stype,
            ),
            None,
            None,
        )
    return None, cands, "AMBIGUOUS_SERIES"


def _episodes_in_season(
    client: httpx.Client, s: Settings, series_id: int, season_number: int
) -> list[dict[str, Any]]:
    r = _sonarr_get(
        client, s, "api/v3/episode", {"seriesId": series_id, "seasonNumber": season_number}
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def _s1e1_episode_id(
    client: httpx.Client, s: Settings, series_id: int
) -> int | None:
    eps = _episodes_in_season(client, s, series_id, 1)
    for e in eps:
        if _int_field(e.get("episodeNumber")) == 1:
            return _int_field(e.get("id"))
    return None


def _select_episode_ids_for_lookups(
    season_episodes: list[dict],
    s1e1_id: int | None,
    include_full_series_packs: bool,
    current_season: int,
    cap: int,
) -> list[int]:
    s_eps = sorted(
        season_episodes,
        key=lambda e: (_int_field(e.get("episodeNumber")) or 0,),
    )
    eids = [_int_field(e.get("id")) for e in s_eps]
    eids = [x for x in eids if x is not None]
    out: list[int] = []
    if include_full_series_packs and current_season > 1 and s1e1_id is not None:
        if s1e1_id not in out:
            out.append(s1e1_id)
    if not eids:
        return out[:cap]
    n = len(eids)
    if n + len(out) <= cap:
        for e in eids:
            if e not in out:
                out.append(e)
    else:
        need = cap - len(out)
        for i in range(need):
            if need == 1:
                idx = 0
            else:
                idx = int(round(i * (n - 1) / (need - 1)))
            eid = eids[idx]
            if eid not in out:
                out.append(eid)
    return out[:cap]


def _sonarr_releases_for_episode(
    client: httpx.Client, s: Settings, episode_id: int
) -> list[dict[str, Any]]:
    r = _sonarr_get(client, s, "api/v3/release", {"episodeId": episode_id})
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def _merge_releases(
    by_ep: list[tuple[int, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    by_guid: dict[str, dict] = {}
    for ep_id, rels in by_ep:
        for rel in rels:
            g = str(rel.get("guid") or "")
            if not g:
                g = f"h:{ep_id}|{rel.get('title')}|{rel.get('indexer')}|{rel.get('size')}"
            if g not in by_guid:
                r2 = {**rel, "_episodeId": ep_id}
                by_guid[g] = r2
    merged = list(by_guid.values())
    merged.sort(
        key=lambda d: (
            -int(d.get("seeders") or 0),
            -int(d.get("leechers") or 0),
            -int(d.get("size") or 0),
        )
    )
    return merged


def _search_then_poll_releases_sonarr(
    client: httpx.Client,
    s: Settings,
    ctx: TVContext,
    season_number: int,
    episode_ids: list[int],
) -> tuple[list[dict[str, Any]], str]:
    note_parts: list[str] = []
    st = ctx.series_type.lower()
    if st == "anime" and episode_ids:
        for chunk in [episode_ids[i : i + 30] for i in range(0, len(episode_ids), 30)]:
            p = _sonarr_post_json(
                client,
                s,
                "api/v3/command",
                {"name": "EpisodeSearch", "episodeIds": chunk},
            )
            p.raise_for_status()
        note_parts.append("EpisodeSearch (anime)")
    else:
        p = _sonarr_post_json(
            client,
            s,
            "api/v3/command",
            {
                "name": "SeasonSearch",
                "seriesId": ctx.series_id,
                "seasonNumber": season_number,
            },
        )
        p.raise_for_status()
        note_parts.append("SeasonSearch")
    deadline = time.monotonic() + s.download_search_wait_s
    poll = s.download_poll_s
    merged: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        by_ep: list[tuple[int, list]] = []
        for eid in episode_ids:
            rels = _sonarr_releases_for_episode(client, s, eid)
            by_ep.append((eid, rels))
        merged = _merge_releases(by_ep)
        if merged:
            break
        time.sleep(poll)
    return merged, " ".join(note_parts)


def run_download_options_tv(
    client: httpx.Client,
    s: Settings,
    query: str,
    season_number: int,
    series_id_opt: int | None,
    include_full_series_packs: bool,
) -> dict:
    tq = " ".join(query.split())
    ctx, ambiguous, err = resolve_series_in_library(client, s, tq, series_id_opt)
    if err == "AMBIGUOUS_SERIES" and ambiguous is not None:
        return {
            "ok": False,
            "error": {
                "code": "AMBIGUOUS_SERIES",
                "message": "Multiple Sonarr series match; pass series_id.",
                "series_candidates": ambiguous,
            },
        }
    if err == "UNKNOWN_SERIES_ID":
        return {
            "ok": False,
            "error": {
                "code": "UNKNOWN_SERIES_ID",
                "message": f"No series with id {series_id_opt} in your Sonarr library.",
            },
        }
    if err == "SERIES_NOT_IN_LIBRARY":
        return {
            "ok": False,
            "error": {
                "code": "SERIES_NOT_IN_LIBRARY",
                "message": "Show not in your Sonarr library; add the series first, then try again.",
            },
        }
    if err is not None or ctx is None:
        return {
            "ok": False,
            "error": {
                "code": err or "RESOLVE_FAILED",
                "message": "Could not resolve series in Sonarr.",
            },
        }
    sdt = _sonarr_get(client, s, f"api/v3/series/{ctx.series_id}", None)
    sdt.raise_for_status()
    sdr = sdt.json()
    if not isinstance(sdr, dict):
        return {
            "ok": False,
            "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "bad series"},
        }
    ctx = TVContext(
        series_id=ctx.series_id,
        series_title=str(sdr.get("title") or sdr.get("sortTitle") or ctx.series_title),
        season_number=season_number,
        series_type=str(sdr.get("seriesType") or "standard"),
    )
    s_eps = _episodes_in_season(client, s, ctx.series_id, season_number)
    if not s_eps:
        return {
            "ok": False,
            "error": {
                "code": "NO_EPISODES_FOR_SEASON",
                "message": (
                    f"No episodes for S{season_number:02d} "
                    "(add/monitor the season in Sonarr)."
                ),
            },
        }
    s1e1 = _s1e1_episode_id(client, s, ctx.series_id) if include_full_series_packs else None
    ep_ids = _select_episode_ids_for_lookups(
        s_eps, s1e1, include_full_series_packs, season_number, s.max_episode_release_lookups
    )
    if not ep_ids:
        return {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "no episode ids"}}

    merged, note = _search_then_poll_releases_sonarr(
        client, s, ctx, season_number, ep_ids
    )
    if not merged:
        return {
            "ok": False,
            "error": {
                "code": "NO_RELEASES",
                "message": (
                    f"No releases after {int(s.download_search_wait_s)}s. Check "
                    "Prowlarr/indexers, filters, or run Interactive Search in "
                    "Sonarr once to verify."
                ),
            },
        }
    out_opts: list[dict[str, Any]] = []
    lim = s.download_options_limit
    for i, rel in enumerate(merged[:lim], start=1):
        sz = int(rel.get("size") or 0)
        ep_id = _int_field(rel.get("_episodeId"))
        g = str(rel.get("guid") or "")
        out_opts.append(
            {
                "rank": i,
                "title": str(rel.get("title") or "Unknown release"),
                "seeders": int(rel.get("seeders") or 0),
                "leechers": int(rel.get("leechers") or 0),
                "size": sz,
                "size_human": human_size(sz),
                "indexer": _indexer_name(rel),
                "guid": g,
                "episode_id": ep_id,
                "approved": bool(rel.get("approved")),
                "downloadAllowed": bool(rel.get("downloadAllowed", True)),
                "rejections": rel.get("rejections") or [],
            }
        )
    return {
        "ok": True,
        "type": "tv",
        "series": {"id": ctx.series_id, "title": ctx.series_title},
        "season": season_number,
        "search": note,
        "episode_lookups_used": ep_ids,
        "options": out_opts,
    }


def _strip_internal_release(rel: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rel.items() if not k.startswith("_")}


def grab_sonarr(client: httpx.Client, s: Settings, episode_id: int, guid: str) -> dict:
    r = _sonarr_get(client, s, "api/v3/release", {"episodeId": episode_id})
    r.raise_for_status()
    data = r.json() if r.is_success else None
    if not isinstance(data, list):
        return {
            "ok": False,
            "error": {
                "code": "UPSTREAM_BAD_RESPONSE",
                "message": "sonarr /release is not a list",
            },
        }
    selected: dict | None = None
    for rel in data:
        if not isinstance(rel, dict):
            continue
        if str(rel.get("guid") or "") == guid:
            selected = _strip_internal_release(rel)
            break
    if not selected:
        return {
            "ok": False,
            "error": {
                "code": "RELEASE_GONE",
                "message": (
                    "That release is no longer in Sonarr’s cache; run "
                    "download-options again."
                ),
            },
        }
    g = _sonarr_post_json(client, s, "api/v3/release", selected)
    try:
        body = g.json() if g.content else {}
    except Exception:  # noqa: BLE001
        body = {}
    if g.status_code >= 400:
        return {
            "ok": False,
            "error": {
                "code": "GRAB_FAILED",
                "message": f"sonarr status {g.status_code}",
            },
        }
    return {"ok": True, "command": body, "app": "sonarr"}


__all__ = [
    "TVContext",
    "resolve_series_in_library",
    "run_download_options_tv",
    "grab_sonarr",
]
