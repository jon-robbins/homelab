from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)


def new_request_id() -> str:
    return uuid4().hex


def require_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    settings = get_settings()
    expected = (settings.api_token or "").strip()
    provided = (credentials.credentials if credentials else "").strip()
    if not expected or not provided or provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return provided
