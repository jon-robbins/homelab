from __future__ import annotations

from fastapi import APIRouter, Depends

from app.agent.loop import AgentLoop
from app.api.auth import require_bearer_token
from app.clients.mcp import McpClient
from app.clients.model import ModelClient
from app.clients.schemas import ChatCompletionRequest
from app.config import get_settings

router = APIRouter(prefix="/v1")


def _build_loop() -> AgentLoop:
    settings = get_settings()
    return AgentLoop(
        settings=settings,
        model_client=ModelClient(
            base_url=settings.model_base_url,
            model_name=settings.model_name,
            timeout_seconds=settings.request_timeout_seconds,
            api_key=settings.model_api_key,
        ),
        mcp_client=McpClient(
            base_url=settings.mcp_url,
            api_token=settings.mcp_api_token,
            timeout_seconds=settings.request_timeout_seconds,
        ),
    )


@router.post("/chat/completions")
def chat_completions(
    request: ChatCompletionRequest, _: str = Depends(require_bearer_token)
) -> dict[str, object]:
    if request.stream:
        return {
            "error": "streaming is not implemented in this phase",
        }
    return _build_loop().run_chat_completion(request)
