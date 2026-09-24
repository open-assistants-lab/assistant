"""In-memory signaling between SubagentCoordinator and AgentLoop.

Replaces middleware-based progress/instruction with a direct
context object. Cancel uses asyncio.Event (instant). Instructions
use asyncio.Queue (real-time). Progress uses a callback (fire-and-forget
DB write). Doom loop detection tracks last 3 tool calls.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal


class SubagentDoomLoopError(RuntimeError):
    """Raised when repeated identical tool calls persist after one correction."""


class SubagentCancelledError(Exception):
    """Raised inside AgentLoop when subagent_ctx.cancel_event is set."""
    def __init__(self, task_id: str, reason: str = "cancelled by supervisor"):
        self.task_id = task_id
        super().__init__(f"Subagent {task_id}: {reason}")


@dataclass(frozen=True, slots=True)
class SubagentRuntimeBlock:
    """Typed terminal governance signal for a child run."""

    tool_name: str
    message: str
    terminal_reason: Literal["blocked"] = "blocked"
    error_code: Literal["approval_required"] = "approval_required"


@dataclass
class SubagentContext:
    """In-memory signaling between Coordinator and AgentLoop.

    cancel_event:  set by supervisor -> AgentLoop raises on next check
    instructions:  supervisor pushes -> AgentLoop drains before each LLM call
    on_progress:   fire-and-forget callback for UI progress updates
    """
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    instructions: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    on_progress: Callable[[int, str, str], Awaitable[None]] | None = None

    _step: int = 0
    _doom_track: list[tuple[str, str]] = field(default_factory=list)
    _doom_nudge_sent: bool = False
    allowed_skill_names: frozenset[str] | None = None
    runtime_block: SubagentRuntimeBlock | None = None
    _task_id: str = ""

    def block_for_approval(self, tool_name: str, message: str) -> None:
        """Record the first runtime approval requirement for this child."""
        if self.runtime_block is None:
            self.runtime_block = SubagentRuntimeBlock(tool_name=tool_name, message=message)

    def record_tool_call(self, name: str, args_json: str) -> int:
        """Record a tool call for doom detection. Returns step number."""
        self._step += 1
        self._doom_track.append((name, args_json))
        if len(self._doom_track) > 3:
            self._doom_track.pop(0)
        return self._step

    @property
    def doom_detected(self) -> bool:
        """True if the last 3 tool calls have identical (name, args_json)."""
        return (
            len(self._doom_track) >= 3
            and len(set(self._doom_track[-3:])) == 1
        )


__all__ = [
    "SubagentContext",
    "SubagentCancelledError",
    "SubagentDoomLoopError",
    "SubagentRuntimeBlock",
]
