# mypy: disable-error-code="assignment"
"""Capabilities API — get/update tool/skill/subagent enable state."""
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from src.http.auth import resolve_user_id
from src.sdk.capabilities import (
    load_user_capabilities,
    save_user_capabilities,
)
from src.storage.paths import DEFAULT_USER_ID, _validate_path_id

router = APIRouter(prefix="/capabilities", tags=["capabilities"])

CAPABILITY_SECTIONS = ("tools", "skills", "subagents")


def _resolve_caps(user_id: str) -> dict[str, Any]:
    return load_user_capabilities(user_id)


def _reset_user_loops(user_id: str) -> None:
    from src.sdk.runner import reset_user_sdk_loops

    reset_user_sdk_loops(user_id)


def _validate_replace_payload(body: dict[str, Any]) -> None:
    _validate_top_level_keys(body)
    for section in CAPABILITY_SECTIONS:
        values = body.get(section, {})
        if not isinstance(values, dict):
            raise HTTPException(status_code=400, detail=f"{section} must be an object")
        for name, value in values.items():
            if not isinstance(value, bool):
                raise HTTPException(status_code=400, detail=f"{section}.{name} must be a boolean")


def _validate_patch_payload(body: dict[str, Any]) -> None:
    _validate_top_level_keys(body)
    for section in CAPABILITY_SECTIONS:
        if section not in body:
            continue
        values = body[section]
        if not isinstance(values, dict):
            raise HTTPException(status_code=400, detail=f"{section} must be an object")
        for name, value in values.items():
            if value is not None and not isinstance(value, bool):
                raise HTTPException(
                    status_code=400,
                    detail=f"{section}.{name} must be a boolean or null",
                )


def _validate_top_level_keys(body: dict[str, Any]) -> None:
    for key in body:
        if key not in CAPABILITY_SECTIONS:
            raise HTTPException(status_code=400, detail=f"unknown capabilities section: {key}")


@router.get("")
async def get_capabilities(
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),
    request: Request = None,
) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    _validate_path_id(user_id, "user_id")
    _validate_path_id(workspace_id, "workspace_id")
    return _resolve_caps(user_id)


@router.put("")
async def replace_capabilities(
    body: dict[str, Any],
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),
    request: Request = None,
) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    _validate_path_id(user_id, "user_id")
    _validate_path_id(workspace_id, "workspace_id")
    _validate_replace_payload(body)

    save_user_capabilities(user_id, body)
    _reset_user_loops(user_id)

    return _resolve_caps(user_id)


@router.patch("")
async def patch_capabilities(
    body: dict[str, Any],
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),
    request: Request = None,
) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    _validate_path_id(user_id, "user_id")
    _validate_path_id(workspace_id, "workspace_id")

    caps = load_user_capabilities(user_id)

    # Apply patch — null removes key (revert to user or default)
    _validate_patch_payload(body)

    for section in CAPABILITY_SECTIONS:
        if section in body:
            if section not in caps:
                caps[section] = {}
            for key, value in body[section].items():
                if value is None:
                    caps[section].pop(key, None)
                else:
                    caps[section][key] = value

    save_user_capabilities(user_id, caps)
    _reset_user_loops(user_id)

    return _resolve_caps(user_id)
