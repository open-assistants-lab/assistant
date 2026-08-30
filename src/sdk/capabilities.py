"""Unified capabilities: tool/skill/subagent enable state per scope."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import yaml

from src.config import get_settings
from src.storage.paths import _validate_path_id

PROFESSIONAL_SERVICE_TOOLS = frozenset(
    {
        "interview_start",
        "interview_ask",
        "interview_finish",
        "design_extract",
        "app_import_csv",
        "app_summarize",
    }
)


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


def user_capabilities_root(user_id: str) -> Path:
    """Return the per-user capabilities directory under application data."""
    safe_user_id = _validate_path_id(user_id, "user_id")
    return Path(get_settings().data_path) / "users" / safe_user_id


def load_user_capabilities(user_id: str) -> dict[str, Any]:
    """Load user-level capabilities for a specific user_id."""
    root = user_capabilities_root(user_id)
    caps = load_capabilities(root)
    sentinel = root / ".legacy_capabilities_migrated"
    if not sentinel.exists():
        migrated_legacy = _migrate_legacy_capabilities(caps)
        migrated_scopes, scopes_complete = _migrate_item_scopes(user_id, root, caps)
        if migrated_legacy or migrated_scopes:
            save_capabilities(root, caps)
        if scopes_complete:
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.touch()
    return caps


def save_user_capabilities(user_id: str, caps: dict[str, Any]) -> None:
    """Save user-level capabilities for a specific user_id."""
    save_capabilities(user_capabilities_root(user_id), caps)


def _migrate_item_scopes(user_id: str, root: Path, caps: dict[str, Any]) -> tuple[bool, bool]:
    """Migrate legacy item_scopes.db rows to user-level booleans, failing selected closed."""
    changed = False
    candidates = [root / "item_scopes.db", Path(get_settings().data_path) / "item_scopes.db"]
    for db_path in candidates:
        if not db_path.exists():
            continue
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT resource_type, resource_name, scope FROM item_scopes WHERE user_id=?",
                (user_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            if "no such table" in str(exc).lower():
                continue
            return changed, False
        finally:
            if conn is not None:
                conn.close()

        for row in rows:
            section = f"{row['resource_type']}s"
            if section not in ("tools", "skills", "subagents"):
                continue
            name = row["resource_name"]
            existing = caps.setdefault(section, {})
            if name in existing:
                continue
            existing[name] = row["scope"] == "all"
            changed = True
    return changed, True


def _migrate_legacy_capabilities(caps: dict[str, Any]) -> bool:
    """Copy explicit legacy false values into user-level capabilities."""
    changed = False
    settings = get_settings()
    data_root = getattr(getattr(settings, "deployment", None), "data_root", None)
    root = Path(data_root) if data_root else Path.home() / "Assistant"
    candidates = [root / "capabilities.yaml"]
    workspaces_dir = root / "Workspaces"
    if workspaces_dir.exists():
        candidates.extend(sorted(workspaces_dir.glob("*/capabilities.yaml")))

    for path in candidates:
        if not path.exists():
            continue
        try:
            legacy = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        for section in ("tools", "skills", "subagents"):
            values = legacy.get(section, {})
            if not isinstance(values, dict):
                continue
            target = caps.setdefault(section, {})
            for name, value in values.items():
                if value is False and name not in target:
                    target[name] = False
                    changed = True
    return changed


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


def _tool_default(
    annotations: dict[str, Any] | None, tool_name: str | None = None
) -> bool:
    """Unconfigured tools are enabled unless explicitly disabled."""
    if tool_name in PROFESSIONAL_SERVICE_TOOLS:
        return False
    # Reserve the email-miner namespace so tools added to that professional
    # family inherit the opt-in stance without another defaults migration.
    if tool_name and tool_name.startswith(("email_miner_", "email_mine_")):
        return False
    return True


def resource_enabled(caps: dict[str, Any], section: str, name: str) -> bool:
    """Return user-level enabled state, failing closed for malformed configured values."""
    values = caps.get(section, {})
    if not isinstance(values, dict) or name not in values:
        return _tool_default(None, name) if section == "tools" else True
    value = values[name]
    if isinstance(value, bool):
        return value
    return False


def tool_enabled(
    caps: dict[str, Any],
    tool_name: str,
    annotations: dict[str, Any] | None = None,
) -> bool:
    """Check if a tool is enabled in the given capabilities."""
    if resource_enabled(caps, "tools", tool_name):
        return True
    tools = caps.get("tools", {})
    return tool_name not in tools and _tool_default(annotations, tool_name)


def set_resource_enabled(
    user_id: str, section: str, name: str, enabled: bool | None
) -> None:
    """Persist or clear one user-level resource setting through the canonical store."""
    caps = load_user_capabilities(user_id)
    values = caps.setdefault(section, {})
    if enabled is None:
        values.pop(name, None)
    else:
        values[name] = enabled
    save_user_capabilities(user_id, caps)
    if section == "tools":
        # Import lazily: runner imports capability predicates during bootstrap.
        from src.sdk.runner import refresh_user_tool_registries

        refresh_user_tool_registries(user_id, {name})


def save_capabilities(root: str | Path, caps: dict[str, Any]) -> None:
    """Save capabilities.yaml to a directory root."""
    path = Path(root) / "capabilities.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(caps, default_flow_style=False, sort_keys=False))
