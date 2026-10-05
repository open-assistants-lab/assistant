"""Subagent completion bus for parent-session feedback and wakeups."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.app_logging import get_logger
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


_logger = get_logger()


class SubagentCompletionBus:
    def __init__(self) -> None:
        self._subscribers: list[tuple[str | None, str | None, CompletionCallback]] = []
        # Issue #114: (task_id, id(callback)) records of successful deliveries,
        # so a retry after a failed subscriber cannot redeliver to the healthy
        # ones. Bounded: when full, the oldest half is dropped (replay then
        # redelivers once, as today, instead of growing forever).
        self._delivered: dict[tuple[str, int], None] = {}

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

    async def publish(self, event: SubagentCompletion) -> bool:
        """Deliver to every matching subscriber.

        One callback's exception neither aborts the others (#114) nor marks
        the event delivered for them: the drain keeps the row retryable, and
        this ledger makes the retry skip the subscribers that already got it,
        so the healthy subscriber is not redelivered the same completion.
        """
        delivered = False
        failed = False
        for user_id, session_id, callback in list(self._subscribers):
            if user_id is not None and user_id != event.user_id:
                continue
            if session_id is not None and session_id != event.session_id:
                continue
            key = (event.task_id, id(callback))
            if key in self._delivered:
                delivered = True
                continue
            try:
                result = callback(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                failed = True
                _logger.warning(
                    "completion.subscriber_failed",
                    {"task_id": event.task_id, "error": str(exc)},
                )
                continue
            delivered = True
            self._delivered[key] = None
            if len(self._delivered) > 4096:
                half = len(self._delivered) // 2
                for stale_key in list(self._delivered)[:half]:
                    self._delivered.pop(stale_key, None)
        return delivered and not failed



completion_bus = SubagentCompletionBus()
