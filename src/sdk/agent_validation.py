"""AgentProfile validation — isolated to avoid circular imports with coordinator."""

from __future__ import annotations

from agentprofile.models import AgentProfile

# Tools that subagents must always have access to
MANDATORY_SUBAGENT_TOOLS: set[str] = set()
OPTIONAL_SKILL_LOAD_TOOL = ""


def _is_subagent_tool(name: str) -> bool:
    return name.startswith("subagent_")


def _is_denied_memory_tool(name: str) -> bool:
    return name.startswith("memory_")


DENIED_SKILL_MANAGEMENT_TOOLS: set[str] = {"skill_delete", "skill_update"}


def validate_agent_profile(
    profile: AgentProfile,
    user_id: str = "default",
    workspace_id: str = "personal",
) -> list[str]:
    """Validate an AgentProfile for subagent use. Returns list of error strings (empty = valid)."""
    from src.sdk.native_tools import get_native_tools

    errors: list[str] = []
    tool_names = {t.name for t in get_native_tools()}

    for name in profile.tools or []:
        if name not in tool_names:
            errors.append(f"Unknown tool: {name}")
        if name.startswith("subagent_"):
            errors.append(f"Subagent tool is not allowed in subagent tools: {name}")
        if _is_denied_memory_tool(name):
            errors.append(f"Memory tool is not allowed in subagent tools: {name}")
        if name in DENIED_SKILL_MANAGEMENT_TOOLS:
            errors.append(f"Skill management tool is not allowed in subagent tools: {name}")

    try:
        from src.skills.registry import get_skill_registry

        skill_registry = get_skill_registry(user_id=user_id)
        for skill_name in profile.skills:
            if skill_registry.get_skill(skill_name) is None:
                errors.append(f"Unknown skill: {skill_name}")
    except Exception:
        pass

    if profile.max_llm_calls <= 0:
        errors.append("max_llm_calls must be positive")
    if profile.cost_limit_usd <= 0:
        errors.append("cost_limit_usd must be positive")
    if profile.timeout_seconds <= 0:
        errors.append("timeout_seconds must be positive")

    return errors


# Backward-compat alias
validate_agent_def = validate_agent_profile
