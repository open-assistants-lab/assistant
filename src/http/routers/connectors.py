# mypy: disable-error-code="assignment"
"""
Connector catalog API — lists available SaaS connectors and their setup details.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from src.app_logging import get_logger
from src.http.auth import resolve_user_id

logger = get_logger()
router = APIRouter(prefix="/connectors", tags=["connectors"])

# Repo-owned connector specs (e.g. gmail.yaml) ship under packages/. When
# running from source and no explicit CONNECTKIT_SPEC_DIR is set, prefer the
# repo dir so the catalog includes repo connectors out of the box. Production
# sets the env var explicitly (packaged specs or a mounted spec dir).
if not os.environ.get("CONNECTKIT_SPEC_DIR"):
    _repo_spec_dir = Path(__file__).resolve().parents[3] / "packages" / "connectkit" / "connectors"
    if _repo_spec_dir.exists():
        os.environ["CONNECTKIT_SPEC_DIR"] = str(_repo_spec_dir)


def _get_catalog(user_id: str) -> list[dict[str, Any]]:
    try:
        from connectkit.bridge import ConnectKitBridge

        bridge = ConnectKitBridge(user_id=user_id)
        result: list[dict[str, Any]] = bridge.list_available()
        return result
    except ImportError:
        return []
    except Exception as e:
        logger.warning("connectors.catalog_error", {"error": str(e)}, user_id=user_id)
        return []


@router.get("/catalog")
async def list_connectors(user_id: str = Query(...), request: Request = None) -> list[dict[str, Any]]:
    user_id = resolve_user_id(request, user_id)
    return _get_catalog(user_id)


@router.get("/catalog/{service}")
async def get_connector(service: str, user_id: str = Query(...), request: Request = None) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    catalog = _get_catalog(user_id)
    match = next((c for c in catalog if c["name"] == service), None)
    if not match:
        raise HTTPException(404, f"Connector not found: {service}")
    return match


@router.post("/connect")
async def connect_service(
    request: Request,
    service: str = Query(...),
    user_id: str = Query(...),
) -> dict[str, Any]:
    """Store user-provided credentials for a service.

    Accepts JSON body with name-value pairs matching the connector's required_fields.
    For OAuth: stores client_id + client_secret (user still authorizes via Connect button).
    For API key: stores the key and marks connected immediately.
    """
    user_id = resolve_user_id(request, user_id)
    try:
        from connectkit.bridge import ConnectKitBridge

        body = await request.json()
        bridge = ConnectKitBridge(user_id=user_id)

        catalog = bridge.list_available()
        spec = next((c for c in catalog if c["name"] == service), None)
        if not spec:
            raise HTTPException(404, f"Connector not found: {service}")

        token_data: dict[str, Any] = {}
        for f in spec.get("required_fields", []):
            name = f["name"]
            if name in body:
                token_data[name] = body[name]

        if not token_data:
            raise HTTPException(400, "No credentials provided")

        bridge.vault.store_token(service, spec["auth_type"], token_data)

        if spec["auth_type"] == "api_key":
            bridge.reload_specs()
            from src.sdk.runner import reset_user_sdk_loops
            reset_user_sdk_loops(user_id)
            return {"status": "connected", "service": service}

        return {
            "status": "configured",
            "service": service,
            "next_step": f"Open /auth/login?service={service}&user_id={user_id} to authorize",
        }

    except ImportError:
        raise HTTPException(501, "ConnectKit is not installed")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("connectors.connect_error", {"error": str(e)}, user_id=user_id)
        raise HTTPException(500, f"Failed to store credentials: {e}")


@router.delete("/disconnect")
async def disconnect_service(
    service: str = Query(...),
    user_id: str = Query(...),
    request: Request = None,
) -> dict[str, Any]:
    """Remove stored credentials for a connected service."""
    user_id = resolve_user_id(request, user_id)
    try:
        from connectkit.bridge import ConnectKitBridge

        bridge = ConnectKitBridge(user_id=user_id)
        if not bridge.vault.is_connected(service):
            raise HTTPException(404, f"Service not connected: {service}")

        bridge.vault.delete_token(service)
        bridge.reload_specs()
        from src.sdk.runner import reset_user_sdk_loops
        reset_user_sdk_loops(user_id)
        return {"status": "disconnected", "service": service}

    except ImportError:
        raise HTTPException(501, "ConnectKit is not installed")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("connectors.disconnect_error", {"error": str(e)}, user_id=user_id)
        raise HTTPException(500, f"Failed to disconnect: {e}")

