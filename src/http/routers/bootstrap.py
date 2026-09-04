"""Desktop bootstrap/readiness endpoint (desktop v0.1, Phase D1 task 6).

Authenticated under the standard identity seam (in desktop mode the launch
token is required). Serves the launch contract the native client needs:
versions, desktop capability profile, migration state, identity, and the
sidecar-owned configuration.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request

from src.http.auth import enforce_user_id
from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(tags=["desktop"])


@router.get("/bootstrap")
async def bootstrap(
    request: Request, user_id: str = DEFAULT_USER_ID
) -> dict[str, object]:
    enforce_user_id(
        user_id, getattr(getattr(request, "state", None), "identity", None)
    )

    from src.config import get_settings
    from src.http.desktop import sidecar_versions
    from src.storage.desktop_migration import migration_state

    cfg = get_settings()
    data_root = os.environ.get(
        "DEPLOYMENT_DATA_ROOT", str(cfg.deployment.data_root or "")
    )
    return {
        "versions": sidecar_versions(),
        "capability_profile": "desktop-v0.1.0",
        "migration": migration_state(Path(data_root) if data_root else Path.home() / "Assistant"),
        "identity": {"user_id": DEFAULT_USER_ID, "workspace": "personal"},
        "sidecar": {
            "mode": cfg.deployment.mode,
            "data_root": data_root,
            "system_dir": os.environ.get("DEPLOYMENT_DATA_PATH", ""),
        },
    }
