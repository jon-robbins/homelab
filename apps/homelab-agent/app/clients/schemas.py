from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.config import get_settings


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    tool_choice: str | dict[str, Any] | None = None

    @model_validator(mode="after")
    def _enforce_size_limits(self) -> ChatCompletionRequest:
        settings = get_settings()
        if len(self.messages) > settings.max_messages:
            raise ValueError(f"messages exceeds the limit of {settings.max_messages}")
        for message in self.messages:
            if message.content and len(message.content) > settings.max_message_chars:
                raise ValueError(
                    f"message content exceeds the limit of {settings.max_message_chars} characters"
                )
        return self


class Choice(BaseModel):
    index: int = 0
    message: dict[str, Any]
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default=0)
    model: str
    choices: list[Choice]
    usage: dict[str, int] | None = None
