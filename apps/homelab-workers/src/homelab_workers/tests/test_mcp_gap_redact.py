from homelab_workers.mcp_gap_closer.redact import redact_text


def test_redacts_bearer_token() -> None:
    raw = "Authorization: Bearer super-secret-token-value"
    out = redact_text(raw)
    assert "super-secret-token-value" not in out
    assert "[REDACTED]" in out


def test_redacts_api_key_assignment() -> None:
    raw = 'API_KEY="abcdEFGH1234567890abcdEFGH1234"'
    out = redact_text(raw)
    assert "abcdEFGH1234567890abcdEFGH1234" not in out


def test_preserves_ordinary_ops_text() -> None:
    raw = "Used docker compose exec sonarr because no MCP tool existed"
    assert redact_text(raw) == raw
