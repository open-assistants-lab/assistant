"""Skills tools — SDK-native implementation.

Skills are on-demand knowledge modules (SKILL.md files) that agents can
load when handling specific task types.

Design:
  1. Skill catalog is injected into the system prompt at startup.
     The agent always knows what skills are available.
  2. When a task matches a skill's description, call skills_load(name).
  3. After creating/editing/deleting a SKILL.md file via files_* tools,
     call skills_reload() to refresh the catalog.
  4. Skill availability is user-level; workspace_id is accepted for compatibility.
"""

from __future__ import annotations

from typing import Any

from src.app_logging import get_logger
from src.sdk.capabilities import load_user_capabilities, resource_enabled
from src.sdk.tools import ToolAnnotations, tool
from src.skills.registry import get_skill_registry
from src.storage.paths import DEFAULT_USER_ID

logger = get_logger()


def _get_registry(user_id: str, workspace_id: str = "personal") -> Any:
    return get_skill_registry(user_id=user_id)


def _load_user_caps(user_id: str) -> dict[str, Any]:
    try:
        return load_user_capabilities(user_id)
    except Exception:
        return {"tools": {}, "skills": {}, "subagents": {}}


def _skill_enabled(caps: dict[str, Any], name: str) -> bool:
    return resource_enabled(caps, "skills", name)


@tool
def skills_load(
    name: str,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
) -> str:
    """Load a skill's full SKILL.md content into context.

    Call this when the current task matches a skill's description from the
    available skills catalog in the system prompt.

    Args:
        name: Skill name (e.g. 'skill-creation', 'autoresearch')
        user_id: User identifier
        workspace_id: Workspace identifier

    Returns:
        The skill's full instructions, or an error if not found.
    """
    try:
        registry = _get_registry(user_id, workspace_id)
    except Exception as exc:
        return str(exc)

    caps = _load_user_caps(user_id)
    skill = registry.get_skill(name)
    if not skill:
        available_names = [
            s["name"] for s in registry.get_all_skills() if _skill_enabled(caps, s["name"])
        ]
        return f"Skill '{name}' not found. Available skills: {', '.join(available_names) or 'none'}."

    if not _skill_enabled(caps, name):
        return f"Skill '{name}' is disabled."

    parts = [
        f"<skill_content name=\"{skill.get('name', name)}\">",
        skill.get("content", "").strip(),
        "</skill_content>",
    ]

    if not skill.get("content"):
        return f"Skill '{name}' exists but has no content."

    registry.mark_skill_loaded(name)

    logger.info(
        "skill.loaded",
        {"name": name},
        user_id=user_id,
    )

    return "\n".join(parts)


skills_load.annotations = ToolAnnotations(
    title="Load Skill", read_only=True, idempotent=True
)


@tool
def skills_reload(
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
) -> str:
    """Reload the skill registry after creating, editing, or deleting SKILL.md files.

    Call this after using files_write, files_edit, or files_delete to create,
    modify, or remove a SKILL.md file. The registry must be reloaded for the
    new or changed skill to appear in the available skills catalog.

    Args:
        user_id: User identifier
        workspace_id: Workspace identifier

    Returns:
        Updated list of available skills with their descriptions.
    """
    try:
        registry = _get_registry(user_id, workspace_id)
        registry.reload()
    except Exception as exc:
        return str(exc)

    caps = _load_user_caps(user_id)
    skills = [s for s in registry.get_all_skills() if _skill_enabled(caps, s.get("name", ""))]
    if not skills:
        return "No skills available."

    parts: list[str] = []
    for s in skills:
        name = s.get("name", "")
        desc = s.get("description", "") or ""
        loaded = " [loaded]" if name in registry.get_loaded_skills() else ""
        parts.append(f"  {name}: {desc}{loaded}")

    logger.info(
        "skill.reloaded",
        {"count": len(skills)},
        user_id=user_id,
    )

    return "Skills reloaded:\n" + "\n".join(parts)


skills_reload.annotations = ToolAnnotations(
    title="Reload Skills", read_only=False, destructive=False, idempotent=True
)
