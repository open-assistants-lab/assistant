"""Capabilities API — simplified stub (capability scoping removed)."""

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("")
async def get_capabilities() -> dict[str, Any]:
    return {}


@router.put("")
async def replace_capabilities() -> dict[str, Any]:
    return {}


@router.patch("")
async def patch_capabilities() -> dict[str, Any]:
    return {}
