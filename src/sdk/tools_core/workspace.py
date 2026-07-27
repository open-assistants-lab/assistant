"""Workspace tools — legacy organizational metadata for compatibility."""

from __future__ import annotations

import re

from src.sdk.tools import tool
from src.sdk.workspace_models import (
    Workspace,
    load_workspace,
    save_workspace,
)
from src.sdk.workspace_models import (
    delete_workspace as _delete_ws,
)
from src.sdk.workspace_models import (
    list_workspaces as _list_ws,
)

_CURRENT_WORKSPACES: dict[str, str] = {}


def _get_current_workspace(user_id: str) -> str:
    return _CURRENT_WORKSPACES.get(user_id, "personal")


@tool
def workspace_create(
    name: str,
    description: str = "",
    instructions: str = "",
) -> str:
    """Create legacy workspace metadata for organizing prompts/configuration.

    Workspaces no longer isolate runtime data. Files, memory, skills, subagents,
    and tool availability are user-level. Chat separation is handled by session_id.
    workspace_id remains as legacy organizational/configuration metadata.

    Args:
        name: Display name for the workspace (e.g. "Q2 Planning")
        description: Short organizational description
        instructions: Optional prompt/configuration notes associated with this workspace ID
    """
    ws = Workspace.from_name(name)
    ws.description = description
    ws.prompt = instructions

    # Create workspace directory structure
    from src.storage.paths import DataPaths
    dp = DataPaths(workspace_id=ws.id)
    dp.workspace_files_dir()
    dp.workspace_memory_dir()
    dp.workspace_subagents_dir()
    dp.workspace_skills_dir()

    save_workspace(ws)

    return (
        f"Workspace '{ws.name}' (id: {ws.id}) created.\n"
        "This is legacy organizational metadata only: user data is user-level, "
        "and chats are session-separated by session_id.\n"
        "It does not create isolated files, conversations, memory, skills, or subagents.\n"
        f"Use workspace_switch('{ws.id}') to apply its optional instructions/configuration."
    )


@tool
def workspace_list() -> str:
    """List legacy organizational workspaces and instruction summaries."""
    workspaces = _list_ws()
    if not workspaces:
        return "No workspaces found. Create one with workspace_create(name)."

    lines = [
        "Available workspaces (legacy organizational metadata).",
        "User data is user-level; chats are separated by session_id.",
    ]
    for ws in workspaces:
        desc = ws.description[:60] + "..." if len(ws.description) > 60 else ws.description
        inst = (
            ws.prompt[:40] + "..."
            if len(ws.prompt) > 40
            else ws.prompt
        )
        lines.append(f"  - {ws.name} (id: {ws.id})")
        if desc:
            lines.append(f"    {desc}")
        if inst:
            lines.append(f"    Instructions: {inst}")
    return "\n".join(lines)


@tool
def workspace_switch(name: str, user_id: str = "default_user") -> str:
    """Switch legacy workspace context for optional instructions/configuration."""
    ws_id = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    ws = load_workspace(ws_id)
    if ws is None:
        for w in _list_ws():
            if w.id == ws_id or w.name.lower() == name.strip().lower():
                ws = w
                break

    if ws is None:
        return f"Workspace '{name}' not found. Use workspace_list() to see available workspaces."

    _CURRENT_WORKSPACES[user_id] = ws.id

    info = (
        f"Switched to workspace: {ws.name}\n"
        "Note: workspace_id is legacy organizational metadata. User data remains "
        "user-level, and chats are session-separated by session_id."
    )
    if ws.prompt:
        info += f"\nInstructions: {ws.prompt}"
    return info


@tool
def workspace_current(user_id: str = "default_user") -> str:
    """Get current legacy workspace metadata and instructions."""
    ws_id = _get_current_workspace(user_id)
    ws = load_workspace(ws_id)
    if ws is None:
        return (
            "Current workspace: Personal (default)\n"
            "Workspace IDs are legacy organizational metadata. User data is user-level; "
            "chats are session-separated by session_id."
        )
    return (
        f"Current workspace: {ws.name} (id: {ws.id})\n"
        "Workspace ID is legacy organizational metadata. User data is user-level; "
        "chats are session-separated by session_id.\n"
        f"Description: {ws.description or '(none)'}\n"
        f"Instructions: {ws.prompt or '(none)'}"
    )


@tool
def workspace_delete(name: str) -> str:
    """Delete legacy workspace metadata only.

    This does not delete files, conversations, memory, skills, subagents, or other
    user-level data. Chat separation is handled by session_id.

    Args:
        name: Workspace name or ID to delete
    """
    ws = load_workspace(name)
    if ws is None:
        for w in _list_ws():
            if w.id == name:
                ws = w
                break

    if ws is None:
        return f"Workspace '{name}' not found."

    if ws.id == "personal":
        return "Cannot delete the default Personal workspace."

    _delete_ws(ws.id)
    return (
        f"Workspace metadata for '{ws.name}' deleted. "
        "User-level data was not deleted."
    )
