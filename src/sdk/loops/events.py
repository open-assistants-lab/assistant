"""Event-driven triggers for the agent loop (loop 3)."""

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
