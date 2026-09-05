"""Subagent completion bus for parent-session feedback and wakeups."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.sdk.subagent_models import SubagentResult

CompletionCallback = Callable[["SubagentCompletion"], Awaitable[None] | None]


@dataclass(frozen=True)
class SubagentCompletion:
    user_id: str
    workspace_id: str
    session_id: str
    task_id: str
    agent_name: str
    status: str
    result: SubagentResult | None = None
    error: str | None = None

    @property
    def output_excerpt(self) -> str:
        if self.result and self.result.output:
            text = self.result.output
        elif self.error:
            text = self.error
        else:
            text = self.status
        return text[:500]

    def message(self) -> str:
        return f"Subagent '{self.agent_name}' finished: {self.output_excerpt}"

    def to_ws_payload(self) -> dict[str, Any]:
        return {
            "type": "subagent_completed",
            "data": {
                "task_id": self.task_id,
                "agent_name": self.agent_name,
                "status": self.status,
                "result": self.result.model_dump(mode="json") if self.result else None,
                "error": self.error,
                "excerpt": self.output_excerpt,
            },
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
        }


class SubagentCompletionBus:
    def __init__(self) -> None:
        self._subscribers: list[tuple[str | None, str | None, CompletionCallback]] = []

    def subscribe(
        self,
        user_id: str | None,
        session_id: str | None,
        callback: CompletionCallback,
    ) -> Callable[[], None]:
        entry = (user_id, session_id, callback)
        self._subscribers.append(entry)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(entry)
            except ValueError:
                pass

        return _unsubscribe

    async def publish(self, event: SubagentCompletion) -> None:
        for user_id, session_id, callback in list(self._subscribers):
            if user_id is not None and user_id != event.user_id:
                continue
            if session_id is not None and session_id != event.session_id:
                continue
            result = callback(event)
            if inspect.isawaitable(result):
                await result


completion_bus = SubagentCompletionBus()
