"""Sync router — manual trigger for read-only provider -> Files/ sync (P1-T2).

v1: manual trigger only (POST /workspaces/{id}/sync). Webhooks/periodic poll
are deliberately deferred; the endpoint is idempotent so re-running is safe.
"""

# mypy: disable-error-code="assignment"

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from src.app_logging import get_logger
from src.http.auth import resolve_user_id
from src.sdk.tools_core.file_sync import (
    ConnectorRevokedError,
    FileSyncer,
    UnknownProviderError,
    get_sync_registry,
)
from src.storage.paths import DEFAULT_USER_ID

logger = get_logger()

router = APIRouter(prefix="/workspaces", tags=["sync"])


@router.post("/{workspace_id}/sync")
async def sync_workspace(
    workspace_id: str,
    provider: str = Query(...),
    folder: str = Query(""),
    user_id: str = Query(DEFAULT_USER_ID),
    request: Request = None,
) -> dict[str, Any]:
    """Pull a provider folder into the workspace Files/ dir (read-only)."""
    user_id = resolve_user_id(request, user_id)

    registry = get_sync_registry()
    if registry.get(provider) is None:
        raise HTTPException(404, f"Unknown sync provider: {provider}")

    syncer = FileSyncer(user_id=user_id, workspace_id=workspace_id, registry=registry)
    try:
        result = await syncer.with_provider(provider, folder)
    except ConnectorRevokedError as e:
        # Surface clearly; FileSyncer guarantees no partial tree on failure.
        raise HTTPException(409, f"Connector not connected: {e.provider}") from e
    except UnknownProviderError as e:
        raise HTTPException(404, f"Unknown sync provider: {provider}") from e
    except Exception as e:
        logger.error(
            "sync.trigger_failed",
            {"provider": provider, "error": str(e), "error_type": type(e).__name__},
            user_id=user_id,
        )
        raise HTTPException(502, f"Sync failed: {e}") from e

    return {
        "workspace_id": workspace_id,
        "provider": result.provider,
        "downloaded": len(result.downloaded),
        "skipped": len(result.skipped),
        "failed": result.failed,
        "files": sorted(result.downloaded + result.skipped),
        "ok": result.ok,
    }
