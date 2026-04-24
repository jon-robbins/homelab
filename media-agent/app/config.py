import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Settings:
    """Environment-backed configuration."""

    media_agent_token: str
    sonarr_base: str
    sonarr_api_key: str
    radarr_base: str
    radarr_api_key: str
    # Optional: direct Prowlarr (GET/POST /api/v1/search) without *arr library
    prowlarr_base: str = ""
    prowlarr_api_key: str = ""
    qbittorrent_base: str = ""
    qbittorrent_username: str = ""
    qbittorrent_password: str = ""
    upstream_timeout_s: float = 5.0
    result_limit: int = 10
    overview_max_chars: int = 500
    library_cache_ttl_s: int = 60
    prowlarr_search_timeout_s: float = 120.0
    # download-options: indexer search + /release cache polling
    download_search_wait_s: float = 90.0
    download_poll_s: float = 2.0
    download_options_limit: int = 10
    max_episode_release_lookups: int = 15
    ollama_base: str = "http://ollama:11434"
    router_model: str = "qwen2.5-coder:7b-instruct-q8_0"
    router_max_retries: int = 3
    router_state_path: str = "/tmp/media-agent-router-state.json"
    router_state_ttl_s: int = 1200

    @property
    def prowlarr_configured(self) -> bool:
        return bool((self.prowlarr_base or "").strip() and (self.prowlarr_api_key or "").strip())

    @property
    def qbittorrent_configured(self) -> bool:
        return bool(
            (self.qbittorrent_base or "").strip()
            and (self.qbittorrent_username or "").strip()
            and (self.qbittorrent_password or "").strip()
        )

    @classmethod
    def from_env(cls) -> "Settings":
        # Align with homelab stack: SONARR_INTERNAL_URL, RADARR_INTERNAL_URL, *_API_KEY
        sonarr = (os.environ.get("SONARR_BASE_URL") or os.environ.get("SONARR_URL") or "").strip()
        radarr = (os.environ.get("RADARR_BASE_URL") or os.environ.get("RADARR_URL") or "").strip()
        token = (os.environ.get("MEDIA_AGENT_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("MEDIA_AGENT_TOKEN is required")
        if not sonarr or not os.environ.get("SONARR_API_KEY", "").strip():
            raise RuntimeError("SONARR_BASE_URL/SONARR_URL and SONARR_API_KEY are required")
        if not radarr or not os.environ.get("RADARR_API_KEY", "").strip():
            raise RuntimeError("RADARR_BASE_URL/RADARR_URL and RADARR_API_KEY are required")

        def _f(k: str, d: str) -> float:
            v = (os.environ.get(k) or "").strip()
            if not v:
                return float(d)
            try:
                return float(v)
            except ValueError:
                return float(d)

        def _i(k: str, d: int) -> int:
            v = (os.environ.get(k) or "").strip()
            if not v:
                return d
            try:
                return int(v)
            except ValueError:
                return d

        pl = (os.environ.get("PROWLARR_BASE_URL") or os.environ.get("PROWLARR_URL") or "").strip()
        qb = (
            os.environ.get("QBITTORRENT_INTERNAL_URL")
            or os.environ.get("QBITTORRENT_URL")
            or ""
        ).strip()
        return cls(
            media_agent_token=token,
            sonarr_base=sonarr.rstrip("/"),
            sonarr_api_key=os.environ["SONARR_API_KEY"].strip(),
            radarr_base=radarr.rstrip("/"),
            radarr_api_key=os.environ["RADARR_API_KEY"].strip(),
            prowlarr_base=pl.rstrip("/"),
            prowlarr_api_key=(os.environ.get("PROWLARR_API_KEY") or "").strip(),
            qbittorrent_base=qb.rstrip("/"),
            qbittorrent_username=(os.environ.get("QBITTORRENT_USERNAME") or "").strip(),
            qbittorrent_password=(os.environ.get("QBITTORRENT_PASSWORD") or "").strip(),
            prowlarr_search_timeout_s=_f("MEDIA_AGENT_PROWLARR_TIMEOUT_S", "120"),
            download_search_wait_s=_f("MEDIA_AGENT_DOWNLOAD_WAIT_S", "90"),
            download_poll_s=_f("MEDIA_AGENT_DOWNLOAD_POLL_S", "2"),
            download_options_limit=_i("MEDIA_AGENT_OPTIONS_LIMIT", 10),
            max_episode_release_lookups=_i("MEDIA_AGENT_MAX_EP_RELEASE_LOOKUPS", 15),
            ollama_base=(os.environ.get("OLLAMA_URL") or "http://ollama:11434").strip().rstrip("/"),
            router_model=(os.environ.get("MEDIA_AGENT_ROUTER_MODEL") or "qwen2.5-coder:7b-instruct-q8_0").strip(),
            router_max_retries=_i("MEDIA_AGENT_ROUTER_MAX_RETRIES", 3),
            router_state_path=(os.environ.get("MEDIA_AGENT_ROUTER_STATE_PATH") or "/tmp/media-agent-router-state.json").strip(),
            router_state_ttl_s=_i("MEDIA_AGENT_ROUTER_STATE_TTL_S", 1200),
        )


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def reset_settings() -> None:
    """Test helper: clear cached settings after env changes."""
    global _settings
    _settings = None
