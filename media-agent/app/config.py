from __future__ import annotations

from pydantic import AliasChoices, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed configuration.

    Uses ``pydantic_settings.BaseSettings`` with ``AliasChoices`` so the primary
    and legacy environment variable names both work (e.g. ``SONARR_BASE_URL``
    and ``SONARR_URL``). The public attribute surface matches the prior
    dataclass-based ``Settings`` one-for-one so call sites need no changes.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=True,
        env_file=None,
    )

    media_agent_token: str = Field(validation_alias="MEDIA_AGENT_TOKEN")
    sonarr_base: str = Field(
        validation_alias=AliasChoices("SONARR_BASE_URL", "SONARR_URL")
    )
    sonarr_api_key: str = Field(validation_alias="SONARR_API_KEY")
    radarr_base: str = Field(
        validation_alias=AliasChoices("RADARR_BASE_URL", "RADARR_URL")
    )
    radarr_api_key: str = Field(validation_alias="RADARR_API_KEY")
    prowlarr_base: str = Field(
        default="",
        validation_alias=AliasChoices("PROWLARR_BASE_URL", "PROWLARR_URL"),
    )
    prowlarr_api_key: str = Field(default="", validation_alias="PROWLARR_API_KEY")
    qbittorrent_base: str = Field(
        default="",
        validation_alias=AliasChoices("QBITTORRENT_INTERNAL_URL", "QBITTORRENT_URL"),
    )
    qbittorrent_username: str = Field(default="", validation_alias="QBITTORRENT_USERNAME")
    qbittorrent_password: str = Field(default="", validation_alias="QBITTORRENT_PASSWORD")

    upstream_timeout_s: float = 5.0
    result_limit: int = 10
    overview_max_chars: int = 500
    library_cache_ttl_s: int = 60

    prowlarr_search_timeout_s: float = Field(
        default=120.0, validation_alias="MEDIA_AGENT_PROWLARR_TIMEOUT_S"
    )
    download_search_wait_s: float = Field(
        default=90.0, validation_alias="MEDIA_AGENT_DOWNLOAD_WAIT_S"
    )
    download_poll_s: float = Field(
        default=2.0, validation_alias="MEDIA_AGENT_DOWNLOAD_POLL_S"
    )
    download_options_limit: int = Field(
        default=10, validation_alias="MEDIA_AGENT_OPTIONS_LIMIT"
    )
    max_episode_release_lookups: int = Field(
        default=15, validation_alias="MEDIA_AGENT_MAX_EP_RELEASE_LOOKUPS"
    )

    ollama_base: str = Field(default="http://ollama:11434", validation_alias="OLLAMA_URL")
    router_model: str = Field(
        default="qwen2.5-coder:7b-instruct-q8_0",
        validation_alias="MEDIA_AGENT_ROUTER_MODEL",
    )
    router_max_retries: int = Field(
        default=3, validation_alias="MEDIA_AGENT_ROUTER_MAX_RETRIES"
    )
    router_state_path: str = Field(
        default="/tmp/media-agent-router-state.json",
        validation_alias="MEDIA_AGENT_ROUTER_STATE_PATH",
    )
    router_state_ttl_s: int = Field(
        default=1200, validation_alias="MEDIA_AGENT_ROUTER_STATE_TTL_S"
    )

    @model_validator(mode="after")
    def _normalize_and_require(self) -> Settings:
        self.media_agent_token = (self.media_agent_token or "").strip()
        self.sonarr_base = (self.sonarr_base or "").strip().rstrip("/")
        self.sonarr_api_key = (self.sonarr_api_key or "").strip()
        self.radarr_base = (self.radarr_base or "").strip().rstrip("/")
        self.radarr_api_key = (self.radarr_api_key or "").strip()
        self.prowlarr_base = (self.prowlarr_base or "").strip().rstrip("/")
        self.prowlarr_api_key = (self.prowlarr_api_key or "").strip()
        self.qbittorrent_base = (self.qbittorrent_base or "").strip().rstrip("/")
        self.qbittorrent_username = (self.qbittorrent_username or "").strip()
        self.qbittorrent_password = (self.qbittorrent_password or "").strip()
        self.ollama_base = (self.ollama_base or "").strip().rstrip("/")
        self.router_model = (self.router_model or "").strip()
        self.router_state_path = (self.router_state_path or "").strip()

        if not self.media_agent_token:
            raise ValueError("MEDIA_AGENT_TOKEN is required")
        if not self.sonarr_base or not self.sonarr_api_key:
            raise ValueError(
                "SONARR_BASE_URL/SONARR_URL and SONARR_API_KEY are required"
            )
        if not self.radarr_base or not self.radarr_api_key:
            raise ValueError(
                "RADARR_BASE_URL/RADARR_URL and RADARR_API_KEY are required"
            )
        return self

    @property
    def prowlarr_configured(self) -> bool:
        return bool(self.prowlarr_base and self.prowlarr_api_key)

    @property
    def qbittorrent_configured(self) -> bool:
        return bool(
            self.qbittorrent_base
            and self.qbittorrent_username
            and self.qbittorrent_password
        )

    @classmethod
    def from_env(cls) -> Settings:
        """Backward-compatible constructor mirroring the old dataclass API."""
        try:
            return cls()
        except ValidationError as e:
            raise RuntimeError(_first_error_message(e)) from e


def _first_error_message(exc: ValidationError) -> str:
    """Pick a single human message out of a pydantic validation error."""
    for err in exc.errors():
        msg = err.get("msg") or ""
        if isinstance(msg, str) and msg:
            for prefix in ("Value error, ", "Assertion failed, "):
                if msg.startswith(prefix):
                    return msg[len(prefix):]
            return msg
    return str(exc)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def reset_settings() -> None:
    """Test helper: clear cached settings after env changes."""
    global _settings
    _settings = None
