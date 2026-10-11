from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    # Unauthenticated so orchestrators/compose healthchecks can probe it without
    # holding the bearer token. All other routes still require authentication.
    return {"ok": True}
