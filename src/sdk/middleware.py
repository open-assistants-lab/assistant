"""Middleware base class for the agent SDK.

Simpler: no Runtime parameter, no hook_config decorator, no generic type parameters.

Middleware hooks execute in order during the agent loop:
    before_agent → before_model → [LLM call] → after_model → [tool execution] → after_agent
"""

from __future__ import annotations

from abc import ABC
from typing import Any

from src.sdk.state import AgentState


class Middleware(ABC):
    """Base middleware class for the agent loop.

    Hooks return None to pass through, or a dict of state updates to apply.
    Override only the hooks you need — default implementations return None.
    """

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def before_agent(self, state: AgentState) -> dict[str, Any] | None:
        """Called once before the agent loop starts. Return state updates or None."""
        return None

    def after_agent(self, state: AgentState) -> dict[str, Any] | None:
        """Called once after the agent loop ends. Return state updates or None."""
        return None

    def before_model(self, state: AgentState) -> dict[str, Any] | None:
        """Called before each LLM call. Return state updates or None."""
        return None

    def after_model(self, state: AgentState) -> dict[str, Any] | None:
        """Called after each LLM call (before tool execution). Return state updates or None."""
        return None

    async def abefore_agent(self, state: AgentState) -> dict[str, Any] | None:
        """Async version of before_agent. Default calls sync version."""
        return self.before_agent(state)

    async def aafter_agent(self, state: AgentState) -> dict[str, Any] | None:
        """Async version of after_agent. Default calls sync version."""
        return self.after_agent(state)

    async def abefore_model(self, state: AgentState) -> dict[str, Any] | None:
        """Async version of before_model. Default calls sync version."""
        return self.before_model(state)

    async def aafter_model(self, state: AgentState) -> dict[str, Any] | None:
        """Async version of after_model. Default calls sync version."""
        return self.after_model(state)

    def wrap_tool_call(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Called before executing a tool call. May modify arguments.

        Args:
            tool_name: Name of the tool being called.
            tool_input: Tool arguments (may be modified by returning new args).

        Returns:
            Modified or original tool arguments.
        """
        return tool_input

    async def guard_tool_call(self, tool_name: str, tool_input: dict[str, Any]) -> Any:
        """Governance gate invoked before tool execution (M4-1, issue #6).

        Args:
            tool_name: Name of the tool about to execute.
            tool_input: Tool arguments.

        Returns:
            None to proceed with execution, or a ToolResult that REPLACES
            execution entirely (synthetic refusal / pending acknowledgment).
            Hook exceptions are swallowed by the loop (emit-only contract).
        """
        return None
