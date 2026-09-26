"""Immutable subagent capability manifests and launch-time preflight."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from agentprofile.models import AgentProfile
from pydantic import BaseModel, ConfigDict

from src.sdk.capabilities import load_user_capabilities, resource_enabled
from src.sdk.native_tools import get_native_tools
from src.skills.registry import get_skill_registry

SAFE_DEFAULT_TOOL_NAMES = frozenset(
    {"files_list", "files_read", "files_glob_search", "files_grep_search"}
)


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
    requested_workspace_id: str | None = None
    tool_selection_mode: ToolSelectionMode
    requested_tools: tuple[str, ...] = ()
    effective_tools: tuple[str, ...] = ()
    requested_skills: tuple[str, ...] = ()
    effective_skills: tuple[str, ...] = ()
    decisions: tuple[CapabilityDecision, ...] = ()
    ready: bool = False

    @property
    def resolved_workspace_id(self) -> str:
        """Execution workspace, falling back for plans created before this field existed."""
        return self.requested_workspace_id or self.workspace_id

    @property
    def canonical_manifest(self) -> dict[str, Any]:
        decision_fields = ("kind", "name", "status", "read_only", "destructive")
        decisions = [
            {key: getattr(decision, key) for key in decision_fields}
            for decision in self.decisions
        ]
        unique_decisions = {
            json.dumps(decision, sort_keys=True, separators=(",", ":")): decision
            for decision in decisions
        }
        return {
            "version": 1,
            "user_id": self.user_id,
            "requested_workspace_id": self.resolved_workspace_id,
            "agent_name": self.agent_name,
            "selection_mode": self.tool_selection_mode.value,
            "requested_tools": sorted(set(self.requested_tools)),
            "effective_tools": sorted(set(self.effective_tools)),
            "requested_skills": sorted(set(self.requested_skills)),
            "effective_skills": sorted(set(self.effective_skills)),
            "decisions": [unique_decisions[key] for key in sorted(unique_decisions)],
        }

    @property
    def canonical_manifest_json(self) -> str:
        return json.dumps(self.canonical_manifest, sort_keys=True, separators=(",", ":"))

    def to_persisted_dict(self) -> dict[str, Any]:
        """Serialize the launch snapshot and its canonical, content-addressed manifest."""
        return {
            **self.model_dump(mode="json"),
            "requested_workspace_id": self.resolved_workspace_id,
            "canonical_manifest": self.canonical_manifest,
        }

    @classmethod
    def from_persisted(cls, data: Mapping[str, Any]) -> SubagentLaunchPlan:
        """Rehydrate a frozen plan, failing closed if the row is inconsistent.

        Integrity scope, stated precisely: this detects *corrupt or partially
        written* rows — a field edited without its hash, or a nested
        ``canonical_manifest`` that disagrees with the row's own top-level
        fields. It is content-addressing, not authentication, so it is not a
        defence against a determined writer who can recompute the hash. The
        backstop against running unapproved authority is the fresh-preflight
        drift re-check in ``SubagentCoordinator.start_with_plan()``.

        The nested ``canonical_manifest`` is compared explicitly because that is
        the dict ``_run_job`` actually executes from; rebuilding it only from the
        top-level fields would leave a nested-only edit invisible.
        """
        payload = dict(data)
        stored_manifest = payload.get("canonical_manifest")
        if not isinstance(stored_manifest, dict):
            raise ValueError("persisted launch plan is missing its canonical manifest")
        decisions = payload.get("decisions") or ()
        if not isinstance(decisions, list | tuple):
            raise ValueError("persisted launch plan has malformed decisions")
        plan = cls(
            plan_id=str(payload.get("plan_id") or ""),
            agent_name=str(payload.get("agent_name") or ""),
            user_id=str(payload.get("user_id") or ""),
            workspace_id=str(payload.get("workspace_id") or ""),
            requested_workspace_id=payload.get("requested_workspace_id"),
            tool_selection_mode=ToolSelectionMode(
                payload.get("tool_selection_mode") or ToolSelectionMode.SAFE_DEFAULT
            ),
            requested_tools=tuple(payload.get("requested_tools") or ()),
            effective_tools=tuple(payload.get("effective_tools") or ()),
            requested_skills=tuple(payload.get("requested_skills") or ()),
            effective_skills=tuple(payload.get("effective_skills") or ()),
            decisions=tuple(CapabilityDecision(**dict(item)) for item in decisions),
            ready=bool(payload.get("ready", False)),
        )
        recomputed = plan.canonical_manifest
        recomputed_id = _plan_id(plan.canonical_manifest_json)
        # An absent hash is not a verified hash: require one explicitly.
        if not plan.plan_id or plan.plan_id != recomputed_id:
            raise SubagentLaunchPlanTampered(
                f"frozen manifest hash {plan.plan_id[:12] or '<missing>'} does not match "
                f"its content ({recomputed_id[:12]})"
            )
        if stored_manifest != recomputed:
            raise SubagentLaunchPlanTampered(
                "stored canonical_manifest disagrees with the row's own fields"
            )
        return plan

    @property
    def rejected_decisions(self) -> tuple[CapabilityDecision, ...]:
        return tuple(decision for decision in self.decisions if not decision.accepted)


class SubagentLaunchRejected(ValueError):  # noqa: N818 - public contract name
    """Preflight rejected a launch before any queue or LLM side effect."""

    code = "capability_unavailable"

    #: The rejected plan, or ``None`` when nothing validated (tampered row).
    #: Catch sites rely on this attribute always existing.
    plan: SubagentLaunchPlan | None

    def __init__(self, plan: SubagentLaunchPlan):
        self.plan = plan
        rejected = ", ".join(
            f"{item.kind}:{item.name} ({item.status})" for item in plan.rejected_decisions
        )
        super().__init__(f"Subagent launch rejected: {rejected or self.code}")


class SubagentManifestDrift(SubagentLaunchRejected):  # noqa: N818 - public contract name
    """A frozen manifest no longer matches the plan resolved from current state.

    Raised instead of launching with either the old or the new authority: an
    unattended schedule must never silently broaden *or* narrow what it may do,
    and re-authorizing it is the operator's decision (#46).
    """

    code = "manifest_drift"

    def __init__(
        self,
        reason: str,
        *,
        frozen: SubagentLaunchPlan | None = None,
        fresh: SubagentLaunchPlan | None = None,
    ) -> None:
        # `plan` must always exist: catch sites (e.g. the subagent_start tool)
        # do `exc.plan.rejected_decisions` on this whole exception family, so a
        # missing attribute would turn a clean refusal into a crash.
        self.plan = frozen
        self.frozen = frozen
        self.fresh = fresh
        ValueError.__init__(self, f"Subagent launch rejected: {reason} ({self.code})")

class SubagentLaunchPlanTampered(SubagentLaunchRejected):  # noqa: N818 - public contract name
    """A persisted launch plan's content does not match its own manifest hash."""

    code = "manifest_tampered"

    def __init__(self, reason: str) -> None:
        # No valid plan exists to attach — the row failed its integrity check.
        self.plan = None
        ValueError.__init__(self, f"Subagent launch rejected: {reason} ({self.code})")


def resolve_permission(user_id: str, tool_name: str, tool_input: dict[str, Any]) -> str:
    """Resolve one effective permission without creating an approval proposal."""
    from src.sdk.governance import get_governance_service

    return get_governance_service(user_id).resolve_permission_for_call(user_id, tool_name, tool_input)


def _plan_id(canonical_manifest_json: str) -> str:
    """Return the full SHA-256 identity of a canonical resolved manifest."""
    return hashlib.sha256(canonical_manifest_json.encode()).hexdigest()


def manifest_drift_reason(
    frozen: SubagentLaunchPlan, fresh: SubagentLaunchPlan
) -> str | None:
    """Describe how a frozen manifest differs from the freshly resolved one.

    ``plan_id`` is the authoritative gate: it is the SHA-256 of the whole
    canonical manifest, so comparing it catches *every* differing field
    automatically. The field-by-field breakdown below is only the
    human-readable explanation — it is deliberately not the security boundary,
    because a hand-maintained field list would silently stop covering anything
    later added to ``canonical_manifest``.
    """
    differences: list[str] = []
    if frozen.plan_id != fresh.plan_id:
        differences.append(f"manifest {frozen.plan_id[:12]} -> {fresh.plan_id[:12]}")
    if frozen.user_id != fresh.user_id:
        differences.append(f"user_id {frozen.user_id} -> {fresh.user_id}")
    if frozen.agent_name != fresh.agent_name:
        differences.append(f"agent_name {frozen.agent_name} -> {fresh.agent_name}")
    if frozen.resolved_workspace_id != fresh.resolved_workspace_id:
        differences.append(
            f"workspace {frozen.resolved_workspace_id} -> {fresh.resolved_workspace_id}"
        )
    if frozen.tool_selection_mode != fresh.tool_selection_mode:
        differences.append(
            f"tool_selection_mode {frozen.tool_selection_mode} -> {fresh.tool_selection_mode}"
        )
    for label, before, after in (
        ("requested_tools", frozen.requested_tools, fresh.requested_tools),
        ("effective_tools", frozen.effective_tools, fresh.effective_tools),
        ("requested_skills", frozen.requested_skills, fresh.requested_skills),
        ("effective_skills", frozen.effective_skills, fresh.effective_skills),
    ):
        gained = sorted(set(after) - set(before))
        lost = sorted(set(before) - set(after))
        if gained:
            differences.append(f"{label} gained {', '.join(gained)}")
        if lost:
            differences.append(f"{label} lost {', '.join(lost)}")
    return "; ".join(differences) or None


def _tool_decision(name: str, tool_map: dict[str, Any], caps: dict[str, Any], user_id: str) -> CapabilityDecision:
    definition = tool_map.get(name)
    if definition is None:
        return CapabilityDecision(kind="tool", name=name, status="missing", reason="not registered")
    if (
        name.startswith("subagent_")
        or name.startswith("memory_")
        or name in {"skill_delete", "skill_update"}
    ):
        return CapabilityDecision(
            kind="tool",
            name=name,
            status="forbidden",
            reason="tool is forbidden for subagents",
        )
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
            name for name in sorted(SAFE_DEFAULT_TOOL_NAMES) if name in tool_map
        )
    elif tool_selection_mode is ToolSelectionMode.NONE:
        selected_tools = ()
    else:
        selected_tools = requested_tools

    decisions: list[CapabilityDecision] = []
    if tool_selection_mode is ToolSelectionMode.SAFE_DEFAULT:
        selected_set = set(selected_tools)
        for name in requested_tools:
            if name in selected_set:
                continue
            decision = _tool_decision(name, tool_map, caps, user_id)
            if decision.accepted:
                decision = decision.model_copy(
                    update={
                        "status": "not_in_safe_default",
                        "reason": "declared tool is outside the safe-default manifest",
                    }
                )
            decisions.append(decision)
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

    plan = SubagentLaunchPlan(
        plan_id="",
        agent_name=profile.name,
        user_id=user_id,
        workspace_id=workspace_id,
        requested_workspace_id=workspace_id,
        tool_selection_mode=tool_selection_mode,
        requested_tools=requested_tools,
        effective_tools=tuple(effective_tools),
        requested_skills=requested_skills,
        effective_skills=tuple(effective_skill_names),
        decisions=tuple(decisions),
        ready=all(decision.accepted for decision in decisions),
    )
    return plan.model_copy(update={"plan_id": _plan_id(plan.canonical_manifest_json)})
