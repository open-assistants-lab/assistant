"""Capabilities API — get/update tool/skill/subagent enable state."""

from typing import Any

from fastapi import APIRouter, Query

from src.sdk.capabilities import (
    load_capabilities,
    merge_capabilities,
    save_capabilities,
)
from src.storage.paths import _validate_path_id, get_paths

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


def _resolve_caps(user_id: str, workspace_id: str) -> dict[str, Any]:
    paths = get_paths(user_id, workspace_id=workspace_id)
    user_caps = load_capabilities(paths.root)
    ws_caps = load_capabilities(paths.root / "Workspaces" / workspace_id)
    return merge_capabilities(user_caps, ws_caps)


@router.get("")
async def get_capabilities(
    user_id: str = Query("default_user"),
    workspace_id: str = Query("personal"),
) -> dict[str, Any]:
    _validate_path_id(user_id, "user_id")
    _validate_path_id(workspace_id, "workspace_id")
    return _resolve_caps(user_id, workspace_id)


@router.put("")
async def replace_capabilities(
    body: dict[str, Any],
    user_id: str = Query("default_user"),
    workspace_id: str = Query("personal"),
) -> dict[str, Any]:
    _validate_path_id(user_id, "user_id")
    _validate_path_id(workspace_id, "workspace_id")

    paths = get_paths(user_id, workspace_id=workspace_id)
    workspace_root = paths.root / "Workspaces" / workspace_id
    save_capabilities(workspace_root, body)

    from src.sdk.runner import reset_sdk_loop

    reset_sdk_loop(user_id, workspace_id)

    return _resolve_caps(user_id, workspace_id)


@router.patch("")
async def patch_capabilities(
    body: dict[str, Any],
    user_id: str = Query("default_user"),
    workspace_id: str = Query("personal"),
) -> dict[str, Any]:
    _validate_path_id(user_id, "user_id")
    _validate_path_id(workspace_id, "workspace_id")

    paths = get_paths(user_id, workspace_id=workspace_id)
    workspace_root = paths.root / "Workspaces" / workspace_id

    # Load current workspace caps
    ws_caps = load_capabilities(workspace_root)

    # Apply patch — null removes key (revert to user or default)
    for section in ("tools", "skills", "subagents"):
        if section in body:
            if section not in ws_caps:
                ws_caps[section] = {}
            for key, value in body[section].items():
                if value is None:
                    ws_caps[section].pop(key, None)
                else:
                    ws_caps[section][key] = value

    save_capabilities(workspace_root, ws_caps)

    from src.sdk.runner import reset_sdk_loop

    reset_sdk_loop(user_id, workspace_id)

    return _resolve_caps(user_id, workspace_id)
