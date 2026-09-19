# mypy: disable-error-code="assignment"
"""Tools API — list tools with metadata, toggle user-level enabled state."""
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from src.http.auth import resolve_user_id
from src.sdk.capabilities import (
    load_user_capabilities,
    resource_enabled,
    set_resource_enabled,
)
from src.sdk.native_tools import get_tool_category
from src.storage.paths import DEFAULT_USER_ID, _validate_path_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])


ScopeKind = str


def _get_registry() -> list[Any]:
    """Get the full tool registry from native tools (lazy, cached)."""
    from src.sdk.native_tools import get_native_tools

    return get_native_tools()


def _load_user_caps(user_id: str) -> dict[str, Any]:
    return load_user_capabilities(user_id)


def _save_user_enabled(user_id: str, section: str, name: str, enabled: bool) -> None:
    set_resource_enabled(user_id, section, name, enabled)


def _scope_response(enabled: bool) -> tuple[ScopeKind, list[str]]:
    return ("all" if enabled else "none", [])


def _tool_enabled(caps: dict[str, Any], name: str) -> bool:
    return resource_enabled(caps, "tools", name)


def _reset_user_loops(user_id: str) -> None:
    from src.sdk.runner import reset_user_sdk_loops

    reset_user_sdk_loops(user_id)


@router.get("")
async def list_tools(
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),
    request: Request = None,
) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    _validate_path_id(user_id, "user_id")
    _validate_path_id(workspace_id, "workspace_id")

    registry = _get_registry()
    caps = _load_user_caps(user_id)

    tools_list = []

    for tool in registry:
        annotations = (
            tool.annotations.model_dump() if hasattr(tool, "annotations") else {}
        )
        category = get_tool_category(tool.name)

        enabled = _tool_enabled(caps, tool.name)
        scope, workspace_ids = _scope_response(enabled)

        tools_list.append(
            {
                "name": tool.name,
                "description": tool.description,
                "category": category,
                "annotations": annotations,
                "parameters": tool.parameters,
                "enabled": enabled,
                "scope": scope,
                "workspace_ids": workspace_ids,
                "source": "native",
            }
        )

    categories_enabled: dict[str, dict[str, Any]] = {}
    for tool_info in tools_list:
        category = tool_info["category"]
        categories_enabled.setdefault(category, {"count": 0, "enabled": 0})
        categories_enabled[category]["count"] += 1
        if tool_info["enabled"]:
            categories_enabled[category]["enabled"] += 1

    # Issue #8: merge the requesting user's custom TOOL.md tools
    # (deployment-shared + per-user dirs) into the listing.
    await _merge_custom_tools(tools_list, categories_enabled, user_id, caps)

    return {"tools": tools_list, "categories": categories_enabled}


async def _merge_custom_tools(
    tools_list: list[dict[str, Any]],
    categories_enabled: dict[str, dict[str, Any]],
    user_id: str,
    caps: dict[str, Any],
) -> None:
    """Issue #8: append the user's custom TOOL.md tools to the listing.

    Same annotation surface as core entries; scope filtered by the user's
    capabilities (custom tools not in the professional-service set default
    enabled). source='custom' marks provenance without breaking the shape.
    """
    from src.sdk.capabilities import resource_enabled
    from src.sdk.tools_custom import get_custom_tools

    try:
        custom = get_custom_tools(user_id)
    except Exception:
        return

    existing = {t["name"] for t in tools_list}
    for tool in custom:
        if tool.name in existing:
            continue
        annotations = (
            tool.annotations.model_dump()
            if getattr(tool, "annotations", None)
            else {}
        )
        enabled = resource_enabled(caps, "tools", tool.name)
        scope, workspace_ids = _scope_response(enabled)
        tools_list.append(
            {
                "name": tool.name,
                "description": tool.description,
                "category": "custom",
                "annotations": annotations,
                "parameters": tool.parameters,
                "enabled": enabled,
                "scope": scope,
                "workspace_ids": workspace_ids,
                "source": "custom",
            }
        )
        rollup = categories_enabled.setdefault(
            "custom", {"count": 0, "enabled": 0}
        )
        rollup["count"] += 1
        if enabled:
            rollup["enabled"] += 1


@router.get("/{name}")
async def get_tool(
    name: str,
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),
    request: Request = None,
) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    _validate_path_id(user_id, "user_id")
    _validate_path_id(workspace_id, "workspace_id")

    registry = _get_registry()

    for tool in registry:
        if tool.name == name:
            annotations = (
                tool.annotations.model_dump()
                if hasattr(tool, "annotations")
                else {}
            )
            caps = _load_user_caps(user_id)
            enabled = _tool_enabled(caps, tool.name)
            scope, wids = _scope_response(enabled)
            return {
                "name": tool.name,
                "description": tool.description,
                "category": get_tool_category(tool.name),
                "annotations": annotations,
                "parameters": tool.parameters,
                "enabled": enabled,
                "scope": scope,
                "workspace_ids": wids,
                "source": "native",
            }

    raise HTTPException(status_code=404, detail=f"Tool not found: {name}")


@router.patch("/{name}")
async def toggle_tool(
    name: str,
    body: dict[str, Any],
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),
    request: Request = None,
) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    """Set a tool's scope.

    New body (preferred):
      {"scope": "all"|"none"}

    Old body (backward compat):
      {"enabled": true/false}
      → enabled=true converts to scope="all"
      → enabled=false converts to scope="none"
    """
    _validate_path_id(user_id, "user_id")
    _validate_path_id(workspace_id, "workspace_id")

    registry = _get_registry()
    if not any(t.name == name for t in registry):
        raise HTTPException(status_code=404, detail=f"Tool not found: {name}")

    if "scope" in body:
        new_scope: ScopeKind = body["scope"]
        if new_scope not in ("all", "selected", "none"):
            raise HTTPException(
                status_code=400,
                detail="scope must be 'all', 'selected', or 'none'",
            )
        if new_scope == "selected":
            raise HTTPException(
                status_code=400,
                detail="workspace-selected scope is no longer supported; use 'all' or 'none'",
            )
        enabled = new_scope != "none"
        _save_user_enabled(user_id, "tools", name, enabled)
        if not enabled:
            # Audit E24-tools: a disabled tool must stop being advertised, and
            # that is already enforced at query time — tool_search filters rows
            # through resource_enabled() (see _eligible_results in
            # src/sdk/tools_core/tool_search.py) and the execution boundary
            # re-checks capabilities. Destroying the row was worse than
            # redundant: non-core native tools are reachable only through their
            # index row, and neither a restart (hashes still match) nor
            # tool_reload (custom + MCP rows only) restores it, so
            # disable-then-enable left the tool permanently "Unknown tool"
            # (review P1 on #27).
            pass
        scope, wids = _scope_response(enabled)
        _reset_user_loops(user_id)
        return {
            "name": name,
            "enabled": enabled,
            "scope": scope,
            "workspace_ids": wids,
        }

    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise HTTPException(status_code=400, detail="enabled must be a boolean")
        enabled_val = body["enabled"]
        _save_user_enabled(user_id, "tools", name, enabled_val)
        scope, wids = _scope_response(enabled_val)
        _reset_user_loops(user_id)
        return {
            "name": name,
            "enabled": enabled_val,
            "scope": scope,
            "workspace_ids": wids,
        }

    raise HTTPException(
        status_code=400, detail="Missing 'scope' or 'enabled' field"
    )
