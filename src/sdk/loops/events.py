"""Event-driven triggers for the agent loop (loop 3).

Trigger types:
    - cron: scheduled check-ins from AgentScheduler
- webhook: external POST /webhooks/{trigger_id}
- file_change: file watcher detects changes
- manual: POST /trigger for testing/automation
- rerun: middleware-triggered re-run (e.g. rubric needs_revision)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.app_logging import get_logger

logger = get_logger()


@dataclass
class AgentEvent:
    """Normalized event that triggers an agent run."""

    trigger_type: str
    trigger_id: str
    user_id: str
    session_id: str
    message: str
    rubric: str | None = None
    model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[AgentEvent], Awaitable[None]]


class TriggerRegistry:
    """Registry of trigger handlers, keyed by trigger type."""

    def __init__(self) -> None:
        self._handlers: dict[str, EventHandler] = {}

    def register(self, trigger_type: str, handler: EventHandler) -> None:
        self._handlers[trigger_type] = handler
        logger.info("trigger_registry.registered", {"trigger_type": trigger_type})

    def unregister(self, trigger_type: str) -> None:
        self._handlers.pop(trigger_type, None)

    async def fire(self, event: AgentEvent) -> None:
        handler = self._handlers.get(event.trigger_type)
        if handler is None:
            raise KeyError(f"No handler registered for trigger type: {event.trigger_type}")
        logger.info(
            "trigger_registry.firing",
            {"trigger_type": event.trigger_type, "trigger_id": event.trigger_id, "user_id": event.user_id},
            user_id=event.user_id,
        )
        await handler(event)


# Global registry singleton
_global_registry: TriggerRegistry | None = None


def get_trigger_registry() -> TriggerRegistry:
    """Get the global trigger registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = TriggerRegistry()
    return _global_registry


async def default_trigger_handler(event: AgentEvent) -> None:
    """Default handler that runs the agent via run_sdk_agent.

    Used by all trigger types (cron, webhook, file_change, manual, rerun).
    For 'rerun' triggers, the event.message is feedback to append to the
    existing conversation before re-running.
    """
    from src.sdk.messages import Message
    from src.sdk.runner import run_sdk_agent

    if event.trigger_type == "rerun":
        # Rerun: append feedback as user message, then re-run with full history
        messages = event.metadata.get("previous_messages", [])
        messages = list(messages) + [Message(role="user", content=event.message, source="rubric_middleware")]
    else:
        messages = [Message.user(event.message)]

    result = await run_sdk_agent(
        user_id=event.user_id,
        messages=messages,
        session_id=event.session_id,
        model=event.model,
        rubric=event.rubric,
    )

    # Store rerun result so the caller can retrieve it
    if event.trigger_type == "rerun":
        event.metadata["_rerun_result"] = result

    response = ""
    for msg in reversed(result):
        if msg.role == "assistant" and isinstance(msg.content, str):
            response = msg.content
            break

    logger.info(
        "trigger_handler.completed",
        {
            "trigger_type": event.trigger_type,
            "trigger_id": event.trigger_id,
            "user_id": event.user_id,
            "response_length": len(response),
        },
        user_id=event.user_id,
    )
