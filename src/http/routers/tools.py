"""Tools API — list tools with metadata, always globally available."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.sdk.native_tools import get_tool_category

router = APIRouter(prefix="/tools", tags=["tools"])


def _get_registry() -> list[Any]:
    """Get the full tool registry from native tools (lazy, cached)."""
    from src.sdk.native_tools import get_native_tools

    return get_native_tools()


@router.get("")
async def list_tools(
    user_id: str = Query("default_user"),
) -> dict[str, Any]:
    registry = _get_registry()

    tools_list = []
    categories_enabled: dict[str, dict[str, Any]] = {}

    for tool in registry:
        annotations = (
            tool.annotations.model_dump() if hasattr(tool, "annotations") else {}
        )
        category = get_tool_category(tool.name)

        tools_list.append(
            {
                "name": tool.name,
                "description": tool.description,
                "category": category,
                "annotations": annotations,
                "parameters": tool.parameters,
                "enabled": True,
                "source": "native",
            }
        )

        if category not in categories_enabled:
            cat_tools = [t for t in registry if get_tool_category(t.name) == category]
            categories_enabled[category] = {
                "count": len(cat_tools),
                "enabled": len(cat_tools),
            }

    return {"tools": tools_list, "categories": categories_enabled}


@router.get("/{name}")
async def get_tool(
    name: str,
    user_id: str = Query("default_user"),
) -> dict[str, Any]:
    registry = _get_registry()

    for tool in registry:
        if tool.name == name:
            annotations = (
                tool.annotations.model_dump()
                if hasattr(tool, "annotations")
                else {}
            )
            return {
                "name": tool.name,
                "description": tool.description,
                "category": get_tool_category(tool.name),
                "annotations": annotations,
                "parameters": tool.parameters,
                "enabled": True,
                "source": "native",
            }

    raise HTTPException(status_code=404, detail=f"Tool not found: {name}")
