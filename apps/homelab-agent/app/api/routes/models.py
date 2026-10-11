from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.auth import require_bearer_token
from app.config import get_settings

router = APIRouter(prefix="/v1")


@router.get("/models")
def models(_: str = Depends(require_bearer_token)) -> dict[str, object]:
    settings = get_settings()
    return {
        "object": "list",
        "data": [
            {
                "id": settings.model_name,
                "object": "model",
                "owned_by": "homelab",
            }
        ],
    }
