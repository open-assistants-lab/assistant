"""Approval middleware for permission-gated tool calls.

PermissionPolicy decides ``allow``, ``ask``, or ``deny``. This middleware
turns ``ask`` into a durable approval proposal and returns a terminal refusal
for ``deny``; the normal tool executor handles ``allow``.
"""

from __future__ import annotations

from typing import Any

from src.sdk.middleware import Middleware
from src.sdk.tools import ToolResult


class HITLMiddleware(Middleware):
    """Enforce the approval branch of the permission policy."""

    def __init__(self, user_id: str = "default_user") -> None:
        self.user_id = user_id

    @property
    def name(self) -> str:
        return "hitl"

    async def guard_tool_call(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> ToolResult | None:
        """Allow, deny, or persist an approval request for one tool call."""
        from src.sdk.governance import get_governance_service, governance_enabled

        if not governance_enabled():
            return None
        svc = get_governance_service(self.user_id)
        permission = svc.resolve_permission_for_call(self.user_id, tool_name, tool_input)
        if permission == "allow":
            return None
        if permission == "deny":
            return ToolResult(
                content=(
                    f"Tool '{tool_name}' is denied by permission policy. "
                    "This call was NOT executed."
                ),
                structured_content={
                    "governance": "deny",
                    "permission": "deny",
                    "tool": tool_name,
                    "executed": False,
                },
                is_error=True,
            )

        # Ask: create a durable proposal. The session/executor snapshot lets
        # approval-after-restart use the same existing resume path.
        session_id: str | None = None
        executor: Any | None = None
        try:
            from src.sdk.loop import _current_agent_loop

            loop = _current_agent_loop.get()
            session_id = getattr(loop, "_flow_session_id", None) if loop else None
            definition = loop._registry.get(tool_name) if loop else None  # noqa: SLF001
            annotations = getattr(definition, "annotations", None)
            if getattr(annotations, "execution_mode", "sync") == "async":
                executor = getattr(annotations, "executor", None)
        except Exception:
            session_id = None
            executor = None
        proposal_id = svc.create_pending(
            self.user_id,
            tool_name,
            tool_input,
            permission="ask",
            session_id=session_id,
            executor=executor,
        )
        return ToolResult(
            content=(
                f"Proposal {proposal_id[:8]} for '{tool_name}' is awaiting "
                "explicit human approval (durable across restarts)."
            ),
            structured_content={
                "governance": "ask",
                "permission": "ask",
                "proposal_id": proposal_id,
                "status": "pending",
            },
        )
