"""HITL middleware — durable approval-gated tools (M4-1/M4-2, issue #6).

Consults the GovernanceService for tier resolution; the loop keeps its
proven pause/resume mechanics (Interrupt + approve_tool_call) — this
middleware adds NO new pause machinery:

- autonomous: pass through
- show_then_auto_send: durable pending created; lazy expiry evaluated at
  read time (no scheduler) — auto-approved unless cancelled
- explicit: durable pending; the loop surfaces an Interrupt for approval
- hard_block: synthetic refusal ToolResult (never an exception)

Governance disabled (default) => the middleware is a complete no-op.
Restart semantics: proposals/receipts are durable always; in-run
replay-resume is deferred to the session-log work (R-SL1) by design.
"""

from __future__ import annotations

import logging
from typing import Any

from src.sdk.middleware import Middleware
from src.sdk.tools import ToolResult

logger = logging.getLogger(__name__)


class HITLMiddleware(Middleware):
    """Tool-call gate backed by GovernanceService tiers + durable pendings."""

    def __init__(self, user_id: str = "default_user") -> None:
        self.user_id = user_id

    @property
    def name(self) -> str:
        return "hitl"

    async def guard_tool_call(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> ToolResult | None:
        """Gate one tool call.

        Returns None to proceed (autonomous / disabled). Returns a synthetic
        ToolResult otherwise: hard_block refusal, or the pending-proposal
        acknowledgment for show_then_auto_send / explicit (the loop's
        Interrupt seam handles the explicit pause — see _execute_tool).
        """
        from src.sdk.governance import (
            get_governance_service,
            governance_enabled,
        )
        from src.sdk.tools import ToolResult

        if not governance_enabled():
            return None
        svc = get_governance_service(self.user_id)
        tier = svc.resolve_tier(self.user_id, tool_name)
        if tier == "autonomous":
            return None
        if tier == "hard_block":
            return ToolResult(
                content=(
                    f"Tool '{tool_name}' is blocked by governance policy "
                    "(hard_block tier). This call was NOT executed."
                ),
                structured_content={
                    "governance": "hard_block",
                    "tool": tool_name,
                    "executed": False,
                },
                is_error=True,
            )
        # explicit / show_then_auto_send -> durable pending proposal.
        # session_log payoff (M4-1 replay-resume): capture the run's session
        # id from the bound loop so approve-after-restart can find the run.
        session_id: str | None = None
        try:
            from src.sdk.loop import _current_agent_loop

            loop = _current_agent_loop.get()
            session_id = getattr(loop, "_flow_session_id", None) if loop else None
        except Exception:
            session_id = None
        proposal_id = svc.create_pending(
            self.user_id, tool_name, tool_input, tier=tier, session_id=session_id
        )
        if tier == "show_then_auto_send":
            # Lazy expiry: the window must ACTUALLY elapse before auto-
            # approval — resolution happens at READ time (the pendings
            # endpoint / a later read), never at creation (M4-1 review P0).
            return ToolResult(
                content=(
                    f"Proposal {proposal_id[:8]} for '{tool_name}' submitted "
                    f"(auto-send window open). Status: pending."
                ),
                structured_content={
                    "governance": "show_then_auto_send",
                    "proposal_id": proposal_id,
                    "status": "pending",
                },
            )
        # explicit: durable pending; the loop's Interrupt seam surfaces it
        return ToolResult(
            content=(
                f"Proposal {proposal_id[:8]} for '{tool_name}' is awaiting "
                "explicit human approval (durable across restarts)."
            ),
            structured_content={
                "governance": "explicit",
                "proposal_id": proposal_id,
                "status": "pending",
            },
        )
