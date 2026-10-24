from homelab_mcp.diagnostics.autobrr_pipeline import diagnose_autobrr_pipeline


def _summary(
    *,
    connected: bool = True,
    healthy: bool = True,
    monitoring: bool = True,
    at_quota: bool = False,
    filter_enabled: bool = True,
    push_error: int = 0,
    filter_rejected: int = 0,
    push_approved: int = 1,
    connection_errors: list[str] | None = None,
    auth_mechanism: str | None = "NONE",
) -> dict:
    irc: dict = {
        "name": "TorrentLeech.org",
        "connected": connected,
        "healthy": healthy,
        "connection_errors": connection_errors or [],
        "channels": [{"name": "#tlannounces", "monitoring": monitoring, "last_announce": "x"}],
    }
    if auth_mechanism is not None:
        irc["auth_mechanism"] = auth_mechanism
    return {
        "irc": [irc],
        "filters": [
            {
                "name": "tl-freeleech-race",
                "enabled": filter_enabled,
                "max_downloads": 5,
                "max_downloads_unit": "DAY",
            }
        ],
        "today": {
            "push_approved": push_approved,
            "push_error": push_error,
            "filter_rejected": filter_rejected,
            "quota_by_filter": [
                {
                    "filter": "tl-freeleech-race",
                    "at_quota": at_quota,
                    "used_today": 5 if at_quota else 0,
                    "remaining_today": 0 if at_quota else 5,
                    "max_downloads": 5,
                }
            ],
        },
        "recent": [],
    }


def test_diagnose_quota_exhausted() -> None:
    out = diagnose_autobrr_pipeline(
        summary=_summary(at_quota=True),
        autobrr_health={"reachable": True, "status": "healthy"},
        qbt_health={"reachable": True, "status": "healthy"},
        push_verify={"checked": 0, "missing_in_qbt": []},
    )
    assert "DAILY_QUOTA_EXHAUSTED" in out["likely_causes"]


def test_diagnose_irc_disconnected() -> None:
    out = diagnose_autobrr_pipeline(
        summary=_summary(connected=False),
        autobrr_health={"reachable": True, "status": "healthy"},
        qbt_health={"reachable": True, "status": "healthy"},
        push_verify={"checked": 0, "missing_in_qbt": []},
    )
    assert "IRC_DISCONNECTED" in out["likely_causes"]


def test_diagnose_push_qbt_mismatch() -> None:
    out = diagnose_autobrr_pipeline(
        summary=_summary(),
        autobrr_health={"reachable": True, "status": "healthy"},
        qbt_health={"reachable": True, "status": "healthy"},
        push_verify={
            "checked": 1,
            "missing_in_qbt": [{"name": "X", "normalized_hash": "abc", "timestamp": "t"}],
        },
    )
    assert "PUSH_QBT_MISMATCH" in out["likely_causes"]


def test_diagnose_autobrr_unreachable() -> None:
    out = diagnose_autobrr_pipeline(
        summary=_summary(),
        autobrr_health={"reachable": False, "status": "down"},
        qbt_health={"reachable": True, "status": "healthy"},
        push_verify={"checked": 0, "missing_in_qbt": []},
    )
    assert "AUTOBRR_UNREACHABLE" in out["likely_causes"]


def test_diagnose_irc_auth_not_none() -> None:
    out = diagnose_autobrr_pipeline(
        summary=_summary(auth_mechanism="SASL"),
        autobrr_health={"reachable": True, "status": "healthy"},
        qbt_health={"reachable": True, "status": "healthy"},
        push_verify={"checked": 0, "missing_in_qbt": []},
    )
    assert "IRC_AUTH_NOT_NONE" in out["likely_causes"]


def test_diagnose_irc_auth_none_is_healthy() -> None:
    out = diagnose_autobrr_pipeline(
        summary=_summary(auth_mechanism="NONE"),
        autobrr_health={"reachable": True, "status": "healthy"},
        qbt_health={"reachable": True, "status": "healthy"},
        push_verify={"checked": 0, "missing_in_qbt": []},
    )
    assert "IRC_AUTH_NOT_NONE" not in out["likely_causes"]
