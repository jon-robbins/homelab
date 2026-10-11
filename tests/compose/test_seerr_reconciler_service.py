from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_seerr_reconciler_service_disabled_by_default():
    media = yaml.safe_load((ROOT / "compose/media.yml").read_text())
    service = media["services"]["homelab-seerr-request-reconciler"]

    env = service["environment"]
    assert "SEERR_RECONCILER_ENABLED=${SEERR_RECONCILER_ENABLED:-0}" in env
    assert "SEERR_RECONCILER_FALLBACK_ENABLED=${SEERR_RECONCILER_FALLBACK_ENABLED:-0}" in env
    assert service["\x62uild"] == {
        "context": "../apps/homelab-workers",
        "dockerfile": "Dockerfile",
    }
    assert service["entrypoint"] == ["homelab-seerr-request-reconciler"]
    assert "command" not in service
    assert set(service["secrets"]) == {
        "QBITTORRENT_USERNAME",
        "QBITTORRENT_PASSWORD",
        "SONARR_API_KEY",
        "RADARR_API_KEY",
        "PROWLARR_API_KEY",
        "OVERSEERR_API_KEY",
    }
    assert "${APP_DATA_DIR:-../data}/seerr-request-reconciler:/state" in service["volumes"]
    assert service["healthcheck"]["test"] == [
        "CMD",
        "homelab-seerr-request-reconciler",
        "--healthcheck",
    ]


def test_mcp_mounts_seerr_reconciler_state_read_only():
    llm = yaml.safe_load((ROOT / "compose/llm.yml").read_text())
    service = llm["services"]["homelab-mcp"]

    env = service["environment"]
    assert "SEERR_RECONCILER_STATUS_PATH=/seerr-request-reconciler/status.json" in env
    assert "SEERR_RECONCILER_STATE_PATH=/seerr-request-reconciler/state.json" in env
    assert (
        "SEERR_RECONCILER_STATUS_MAX_AGE_SECONDS=${SEERR_RECONCILER_STATUS_MAX_AGE_SECONDS:-900}"
        in env
    )
    assert (
        "${APP_DATA_DIR:-../data}/seerr-request-reconciler:/seerr-request-reconciler:ro"
        in service["volumes"]
    )


def test_setup_prepares_seerr_reconciler_state_dir():
    setup = (ROOT / "scripts/ops/setup.sh").read_text()

    assert "data/seerr-request-reconciler" in setup
    assert "chown" in setup
