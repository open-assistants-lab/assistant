# mypy: disable-error-code="assignment"
"""Tools API — list tools with metadata, toggle user-level enabled state."""
from typing import Any
import logging

from fastapi import APIRouter, HTTPException, Query, Request

from src.http.auth import enforce_user_id
from src.sdk.capabilities import load_user_capabilities, resource_enabled, save_user_capabilities
from src.sdk.native_tools import get_tool_category
from src.storage.paths import _validate_path_id

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
    caps = load_user_capabilities(user_id)
    caps.setdefault(section, {})[name] = enabled
    save_user_capabilities(user_id, caps)


def _scope_response(enabled: bool) -> tuple[ScopeKind, list[str]]:
    return ("all" if enabled else "none", [])


def _tool_enabled(caps: dict[str, Any], name: str) -> bool:
    return resource_enabled(caps, "tools", name)


def _reset_user_loops(user_id: str) -> None:
    from src.sdk.runner import reset_user_sdk_loops

    reset_user_sdk_loops(user_id)


def _purge_tool_index_entry(user_id: str, workspace_id: str, name: str) -> None:
    """Remove a disabled tool's row from the persisted search index (audit E24).

    Without this the stale row keeps advertising the tool via tool_search even
    after scope=none. Best-effort: a failure here must not fail the PATCH —
    the execution-boundary caps check still blocks the tool.
    """
    try:
        from src.sdk.tool_index import get_or_create_index
        from src.storage.paths import get_paths

        paths = get_paths(user_id=user_id, workspace_id=workspace_id)
        idx, _commit = get_or_create_index(
            paths.user_tools_dir(),
            None,
            paths.user_mcp_config(),
            user_id=user_id,
            workspace_id=workspace_id,
        )
        idx.remove_tool(name)
        logger.info("tools.index_entry_purged", {"tool": name})
    except Exception as e:
        logger.warning("tools.index_entry_purge_failed", {"tool": name, "error": str(e)})


@router.get("")
async def list_tools(
    user_id: str = Query("default_user"),
    workspace_id: str = Query("personal"),
    request: Request = None,
) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
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

    return {"tools": tools_list, "categories": categories_enabled}


@router.get("/{name}")
async def get_tool(
    name: str,
    user_id: str = Query("default_user"),
    workspace_id: str = Query("personal"),
    request: Request = None,
) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
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
    user_id: str = Query("default_user"),
    workspace_id: str = Query("personal"),
    request: Request = None,
) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
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
            # Audit E24-tools: purge the stale persisted-index row so the
            # disabled tool stops being advertised via tool_search.
            _purge_tool_index_entry(user_id, workspace_id, name)
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
