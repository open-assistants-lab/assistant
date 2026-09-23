"""Pure allow/ask/deny policy resolution for executable items."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

Permission = Literal["allow", "ask", "deny"]

_PERMISSION_ALIASES = {"allow": "allow", "ask": "ask", "deny": "deny"}
_PERMISSION_STRENGTH: dict[Permission, int] = {"allow": 0, "ask": 1, "deny": 2}


def _normalize_permission(value: str) -> Permission:
    return _PERMISSION_ALIASES.get(value, "ask")  # type: ignore[return-value]


def _subject_for_call(tool_name: str, tool_input: Mapping[str, Any]) -> tuple[str, str] | None:
    if tool_name == "skills_load":
        name = tool_input.get("name")
        return ("skills", str(name)) if name else None
    if tool_name in {"subagent_start", "subagent_delegate"}:
        name = tool_input.get("agent_name")
        return ("subagents", str(name)) if name else None
    return None


def _matching_values(
    assignments: Mapping[str, Mapping[str, str]],
    tool_name: str,
    tool_input: Mapping[str, Any],
) -> list[str]:
    values: list[str] = []
    tools = assignments.get("tools", {})
    if isinstance(tools, Mapping) and tool_name in tools:
        values.append(str(tools[tool_name]))
    subject = _subject_for_call(tool_name, tool_input)
    if subject is not None:
        subject_type, subject_name = subject
        items = assignments.get(subject_type, {})
        if isinstance(items, Mapping) and subject_name in items:
            values.append(str(items[subject_name]))
    return values


@dataclass(frozen=True)
class PermissionPolicy:
    """Resolve effective permissions without persistence or side effects.

    Administrator item rules replace the fallback for that item. User rules
    can only make the result stricter: an ``allow`` cannot weaken a fallback
    ``ask`` or an administrator ``deny``.
    """

    admin: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    user: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def resolve(
        self,
        tool_name: str,
        tool_input: Mapping[str, Any],
        *,
        fallback: str = "allow",
    ) -> Permission:
        admin_values = _matching_values(self.admin, tool_name, tool_input)
        user_values = _matching_values(self.user, tool_name, tool_input)
        candidates = (
            [_normalize_permission(value) for value in admin_values]
            if admin_values
            else [_normalize_permission(fallback)]
        )
        candidates.extend(_normalize_permission(value) for value in user_values)
        return max(candidates, key=_PERMISSION_STRENGTH.__getitem__)
