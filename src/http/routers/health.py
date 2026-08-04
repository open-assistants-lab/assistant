from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, Any]:
    """Health check."""
    return {"status": "healthy"}


@router.get("/health/ready")
async def ready() -> dict[str, Any]:
    """Readiness check."""
    return {"status": "ready"}


# NOTE: a GET /models route is intentionally NOT defined here. An earlier
# version exposed the models.dev registry grouped by provider, but it
# shadowed the conversation router's GET /models (registered later) which the
# chat clients depend on for the flat {"models": [...]} catalog. Use the
# /settings/model-catalog endpoint for the grouped provider view instead.
