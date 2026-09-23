"""Immutable subagent capability manifests and launch-time preflight."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from agentprofile.models import AgentProfile
from pydantic import BaseModel, ConfigDict

from src.sdk.capabilities import load_user_capabilities, resource_enabled
from src.sdk.native_tools import get_native_tools
from src.skills.registry import get_skill_registry


class ToolSelectionMode(StrEnum):
    """How a subagent profile selects its available tools."""

    SAFE_DEFAULT = "safe_default"
    NONE = "none"
    ALLOWLIST = "allowlist"
    LEGACY = "legacy"


class CapabilityDecision(BaseModel):
    """One resolved requested capability and its preflight disposition."""

    model_config = ConfigDict(frozen=True)

    kind: str
    name: str
    status: str
    reason: str
    read_only: bool | None = None
    destructive: bool | None = None

    @property
    def accepted(self) -> bool:
        return self.status == "allowed"


class SubagentLaunchPlan(BaseModel):
    """Frozen manifest used to create and execute one subagent task."""

    model_config = ConfigDict(frozen=True)

    plan_id: str
    agent_name: str
    user_id: str
    workspace_id: str
    tool_selection_mode: ToolSelectionMode
    requested_tools: tuple[str, ...] = ()
    effective_tools: tuple[str, ...] = ()
    requested_skills: tuple[str, ...] = ()
    effective_skills: tuple[str, ...] = ()
    decisions: tuple[CapabilityDecision, ...] = ()
    ready: bool = False

    @property
    def rejected_decisions(self) -> tuple[CapabilityDecision, ...]:
        return tuple(decision for decision in self.decisions if not decision.accepted)


class SubagentLaunchRejected(ValueError):  # noqa: N818 - public contract name
    """Preflight rejected a launch before any queue or LLM side effect."""

    code = "capability_unavailable"

    def __init__(self, plan: SubagentLaunchPlan):
        self.plan = plan
        rejected = ", ".join(
            f"{item.kind}:{item.name} ({item.status})" for item in plan.rejected_decisions
        )
        super().__init__(f"Subagent launch rejected: {rejected or self.code}")


def resolve_permission(user_id: str, tool_name: str, tool_input: dict[str, Any]) -> str:
    """Resolve one effective permission without creating an approval proposal."""
    from src.sdk.governance import get_governance_service

    return get_governance_service(user_id).resolve_permission_for_call(user_id, tool_name, tool_input)


def _plan_id(
    profile: AgentProfile,
    user_id: str,
    workspace_id: str,
    mode: ToolSelectionMode,
) -> str:
    payload = {
        "agent": profile.name,
        "user": user_id,
        "workspace": workspace_id,
        "mode": mode.value,
        "tools": list(profile.tools),
        "skills": list(profile.skills),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def _tool_decision(name: str, tool_map: dict[str, Any], caps: dict[str, Any], user_id: str) -> CapabilityDecision:
    definition = tool_map.get(name)
    if definition is None:
        return CapabilityDecision(kind="tool", name=name, status="missing", reason="not registered")
    annotations = definition.annotations
    metadata = {
        "read_only": annotations.read_only,
        "destructive": annotations.destructive,
    }
    if not resource_enabled(caps, "tools", name):
        return CapabilityDecision(status="disabled", reason="disabled by capability policy", name=name, kind="tool", **metadata)
    permission = resolve_permission(user_id, name, {})
    if permission != "allow":
        return CapabilityDecision(
            kind="tool",
            name=name,
            status=f"permission_{permission}",
            reason=f"permission policy resolved to {permission}",
            **metadata,
        )
    return CapabilityDecision(kind="tool", name=name, status="allowed", reason="authorized", **metadata)


def _skill_decision(name: str, registry: Any, caps: dict[str, Any], user_id: str) -> CapabilityDecision:
    if registry.get_skill(name) is None:
        return CapabilityDecision(kind="skill", name=name, status="missing", reason="not in skill registry")
    if not resource_enabled(caps, "skills", name):
        return CapabilityDecision(
            kind="skill", name=name, status="disabled", reason="disabled by capability policy"
        )
    permission = resolve_permission(user_id, "skills_load", {"name": name})
    if permission != "allow":
        return CapabilityDecision(
            kind="skill",
            name=name,
            status=f"permission_{permission}",
            reason=f"permission policy resolved to {permission}",
        )
    return CapabilityDecision(kind="skill", name=name, status="allowed", reason="authorized")


def build_launch_plan(
    profile: AgentProfile,
    user_id: str,
    workspace_id: str,
    tool_selection_mode: ToolSelectionMode,
) -> SubagentLaunchPlan:
    """Resolve every requested child capability before a task can start.

    The plan deliberately gathers all errors. Callers can show a single
    actionable rejection and must not silently narrow a profile's declared
    authority.
    """
    tool_map = {tool.name: tool for tool in get_native_tools()}
    caps = load_user_capabilities(user_id)
    requested_tools = tuple(dict.fromkeys(profile.tools))
    requested_skills = tuple(dict.fromkeys(profile.skills))

    if tool_selection_mode is ToolSelectionMode.SAFE_DEFAULT:
        selected_tools = tuple(
            name
            for name, definition in sorted(tool_map.items())
            if definition.annotations.read_only
            and not definition.annotations.requires_approval
            and not definition.annotations.open_world
            and not name.startswith("subagent_")
        )
    elif tool_selection_mode is ToolSelectionMode.NONE:
        selected_tools = ()
    else:
        selected_tools = requested_tools

    decisions: list[CapabilityDecision] = []
    effective_tools: list[str] = []
    for name in selected_tools:
        decision = _tool_decision(name, tool_map, caps, user_id)
        decisions.append(decision)
        if decision.accepted:
            effective_tools.append(name)

    try:
        registry = get_skill_registry(user_id=user_id)
    except Exception as exc:
        registry = None
        if requested_skills:
            decisions.append(
                CapabilityDecision(
                    kind="skill_registry",
                    name="skills",
                    status="registry_error",
                    reason=str(exc),
                )
            )

    effective_skill_names: list[str] = []
    if registry is not None:
        for name in requested_skills:
            decision = _skill_decision(name, registry, caps, user_id)
            decisions.append(decision)
            if decision.accepted:
                effective_skill_names.append(name)

    if requested_skills:
        skill_loader = _tool_decision("skills_load", tool_map, caps, user_id)
        decisions.append(skill_loader)
        if skill_loader.accepted and "skills_load" not in effective_tools:
            effective_tools.append("skills_load")

    return SubagentLaunchPlan(
        plan_id=_plan_id(profile, user_id, workspace_id, tool_selection_mode),
        agent_name=profile.name,
        user_id=user_id,
        workspace_id=workspace_id,
        tool_selection_mode=tool_selection_mode,
        requested_tools=requested_tools,
        effective_tools=tuple(effective_tools),
        requested_skills=requested_skills,
        effective_skills=tuple(effective_skill_names),
        decisions=tuple(decisions),
        ready=all(decision.accepted for decision in decisions),
    )
