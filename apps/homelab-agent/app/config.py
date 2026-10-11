from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Mounted by compose via `secrets:` (Docker /run/secrets). Prod reads the tokens
# from here; tests inject the env vars, which take precedence.
API_TOKEN_SECRET_FILE = Path("/run/secrets/HOMELAB_AGENT_API_TOKEN")
MCP_API_TOKEN_SECRET_FILE = Path("/run/secrets/MCP_API_TOKEN")
VLLM_API_KEY_SECRET_FILE = Path("/run/secrets/VLLM_API_KEY")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    api_token: str = Field(default="", validation_alias="HOMELAB_AGENT_API_TOKEN")
    mcp_api_token: str = Field(default="", validation_alias="MCP_API_TOKEN")
    model_api_key: str = Field(default="", validation_alias="VLLM_API_KEY")
    model_base_url: str = Field(
        default="http://qwen:18020/v1",
        validation_alias="HOMELAB_AGENT_MODEL_BASE_URL",
    )
    model_name: str = Field(default="qwen3.8-27b", validation_alias="HOMELAB_AGENT_MODEL_NAME")
    mcp_url: str = Field(default="http://homelab-mcp:8090", validation_alias="HOMELAB_AGENT_MCP_URL")
    audit_path: str = Field(default="/data/audit.jsonl", validation_alias="HOMELAB_AGENT_AUDIT_PATH")
    max_tool_steps: int = Field(default=6, validation_alias="HOMELAB_AGENT_MAX_TOOL_STEPS")
    request_timeout_seconds: int = Field(
        default=120,
        validation_alias="HOMELAB_AGENT_REQUEST_TIMEOUT_SECONDS",
    )
    allowed_tools: str = Field(default="", validation_alias="HOMELAB_AGENT_ALLOWED_TOOLS")
    max_messages: int = Field(default=100, validation_alias="HOMELAB_AGENT_MAX_MESSAGES")
    max_message_chars: int = Field(
        default=65536, validation_alias="HOMELAB_AGENT_MAX_MESSAGE_CHARS"
    )
    max_tool_output_chars: int = Field(
        default=8192, validation_alias="HOMELAB_AGENT_MAX_TOOL_OUTPUT_CHARS"
    )
    max_response_chars: int = Field(
        default=65536, validation_alias="HOMELAB_AGENT_MAX_RESPONSE_CHARS"
    )

    def allowlist(self) -> list[str]:
        return [item.strip() for item in self.allowed_tools.split(",") if item.strip()]

    @model_validator(mode="after")
    def _resolve_api_token_from_secret_file(self) -> Settings:
        # Env (validation_alias above) wins; fall back to the compose-mounted
        # secret file only when no token was provided through the environment.
        if not self.api_token and API_TOKEN_SECRET_FILE.is_file():
            token = API_TOKEN_SECRET_FILE.read_text(encoding="utf-8").strip()
            if token:
                self.api_token = token
        if not self.mcp_api_token and MCP_API_TOKEN_SECRET_FILE.is_file():
            token = MCP_API_TOKEN_SECRET_FILE.read_text(encoding="utf-8").strip()
            if token:
                self.mcp_api_token = token
        if not self.model_api_key and VLLM_API_KEY_SECRET_FILE.is_file():
            key = VLLM_API_KEY_SECRET_FILE.read_text(encoding="utf-8").strip()
            if key:
                self.model_api_key = key
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
