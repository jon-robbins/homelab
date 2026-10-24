from __future__ import annotations

from collections.abc import Sequence
from typing import Any

_RECOMMENDATIONS: dict[str, str] = {
    "IRC_DISCONNECTED": "IRC is disconnected; check autobrr IRC network settings and TL nick auth.",
    "IRC_UNHEALTHY": "IRC network reports unhealthy; inspect autobrr logs and TL connectivity.",
    "IRC_CHANNEL_NOT_MONITORING": "Announce channel is not monitoring; re-attach #tlannounces in autobrr.",
    "IRC_CONNECTION_ERRORS": "IRC connection errors present; review autobrr IRC logs.",
    "IRC_AUTH_NOT_NONE": (
        "IRC auth mechanism is not NONE; TorrentLeech advisory expects unauthenticated IRC "
        "(auth.mechanism=NONE). Review autobrr IRC network auth settings."
    ),
    "FILTER_DISABLED": "Ratio filter is disabled; enable tl-freeleech-race in autobrr if racing should be active.",
    "DAILY_QUOTA_EXHAUSTED": "Daily cap reached; new grabs resume after local midnight.",
    "RECENT_PUSH_ERRORS": "Recent push errors; verify qBittorrent client config in autobrr actions.",
    "RECENT_FILTER_REJECTIONS": (
        "Releases are being rejected by filter rules; review filter criteria and recent rejections."
    ),
    "PUSH_QBT_MISMATCH": (
        "autobrr approved pushes not found in qBittorrent; check the qBT client, paths, and categories."
    ),
    "AUTOBRR_UNREACHABLE": "autobrr health probe failed; verify container on homelab_net.",
    "QBITTORRENT_UNREACHABLE": "qBittorrent health probe failed; verify qbittorrent container and credentials.",
}


def diagnose_autobrr_pipeline(
    *,
    summary: dict[str, Any],
    autobrr_health: dict[str, Any],
    qbt_health: dict[str, Any],
    push_verify: dict[str, Any],
    extra_causes: Sequence[tuple[str, str]] = (),
) -> dict[str, Any]:
    causes: list[str] = []
    extra_recommendations = dict(extra_causes)

    if not autobrr_health.get("reachable"):
        causes.append("AUTOBRR_UNREACHABLE")
    if not qbt_health.get("reachable"):
        causes.append("QBITTORRENT_UNREACHABLE")

    for net in summary.get("irc") or []:
        if not net.get("connected"):
            causes.append("IRC_DISCONNECTED")
        if not net.get("healthy"):
            causes.append("IRC_UNHEALTHY")
        if net.get("connection_errors"):
            causes.append("IRC_CONNECTION_ERRORS")
        auth_mechanism = net.get("auth_mechanism")
        if auth_mechanism is not None and str(auth_mechanism).upper() != "NONE":
            causes.append("IRC_AUTH_NOT_NONE")
        for ch in net.get("channels") or []:
            if ch.get("monitoring") is False:
                causes.append("IRC_CHANNEL_NOT_MONITORING")

    for filt in summary.get("filters") or []:
        if filt.get("enabled") is False:
            causes.append("FILTER_DISABLED")

    today = summary.get("today") or {}
    if int(today.get("push_error") or 0) > 0:
        causes.append("RECENT_PUSH_ERRORS")
    if int(today.get("filter_rejected") or 0) > 0 and int(today.get("push_approved") or 0) == 0:
        causes.append("RECENT_FILTER_REJECTIONS")
    for row in today.get("quota_by_filter") or []:
        if row.get("at_quota"):
            causes.append("DAILY_QUOTA_EXHAUSTED")
            break

    if push_verify.get("missing_in_qbt"):
        causes.append("PUSH_QBT_MISMATCH")
    causes.extend(code for code, _ in extra_causes)

    seen: set[str] = set()
    unique_causes: list[str] = []
    for code in causes:
        if code not in seen:
            seen.add(code)
            unique_causes.append(code)

    known = {**_RECOMMENDATIONS, **extra_recommendations}
    recommendations = [known[code] for code in unique_causes if code in known]

    return {
        "likely_causes": unique_causes,
        "recommendations": recommendations,
    }
