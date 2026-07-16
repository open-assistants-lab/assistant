"""Unified capabilities: tool/skill/subagent enable state per scope."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_capabilities(root: str | Path) -> dict[str, Any]:
    """Load capabilities.yaml from a directory root.

    Returns empty defaults if file doesn't exist.
    """
    path = Path(root) / "capabilities.yaml"
    if not path.exists():
        return {"tools": {}, "skills": {}, "subagents": {}}
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("tools", {})
    data.setdefault("skills", {})
    data.setdefault("subagents", {})
    return data


def merge_capabilities(
    user_caps: dict[str, Any], workspace_caps: dict[str, Any]
) -> dict[str, Any]:
    """Merge workspace capabilities over user capabilities.

    Workspace keys override user keys. Missing keys inherit from user.
    """
    merged: dict[str, Any] = {}
    for section in ("tools", "skills", "subagents"):
        user_section = user_caps.get(section, {})
        ws_section = workspace_caps.get(section, {})
        merged[section] = {**user_section, **ws_section}
    return merged


def _tool_default(annotations: dict[str, Any] | None) -> bool:
    """Derive default enabled state from tool annotations.

    Destructive tools default to disabled (safety over convenience).
    Read-only tools default to enabled.
    """
    if not annotations:
        return True
    destructive = annotations.get("destructive", False)
    if destructive:
        return False
    return True


def tool_enabled(
    caps: dict[str, Any],
    tool_name: str,
    annotations: dict[str, Any] | None = None,
) -> bool:
    """Check if a tool is enabled in the given capabilities."""
    tools = caps.get("tools", {})
    if tool_name in tools:
        return tools[tool_name] is not False
    return _tool_default(annotations)


def save_capabilities(root: str | Path, caps: dict[str, Any]) -> None:
    """Save capabilities.yaml to a directory root."""
    path = Path(root) / "capabilities.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(caps, default_flow_style=False, sort_keys=False))
