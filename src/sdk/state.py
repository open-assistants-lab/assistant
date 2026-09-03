"""Agent state passed through the agent loop and middleware.

Drop-in replacement for LangGraph's AgentState (TypedDict).
Uses a simple dataclass with an extra dict for extensibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.sdk.messages import Message


@dataclass
class AgentState:
    """State container for the agent loop.

    Attributes:
        messages: Conversation message history.
        extra: Arbitrary key-value state used by middleware
               (e.g., memory_context, skills_loaded, turn_count).
    """

    messages: list[Message] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    # Session-log observer (R-SL1, P1-T11): set by the loop; every model-
    # visible message appended via add_message is forwarded. Emit-only.
    message_observer: Any = None

    def get(self, key: str, default: Any = None) -> Any:
        return self.extra.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.extra[key] = value

    def update(self, updates: dict[str, Any]) -> None:
        for key, value in updates.items():
            if key == "messages":
                if isinstance(value, list):
                    self.messages = value
            elif key == "extra":
                if isinstance(value, dict):
                    self.extra.update(value)
            else:
                self.extra[key] = value

    def add_message(self, message: Message) -> None:
        self.messages.append(message)
        observer = self.message_observer
        if observer is not None:
            try:
                observer(message)
            except Exception:
                pass  # emit-only: logging must never break the loop

    def last_message(self) -> Message | None:
        return self.messages[-1] if self.messages else None

    def message_count(self) -> int:
        return len(self.messages)

    def user_messages(self) -> list[Message]:
        return [m for m in self.messages if m.role == "user"]

    def assistant_messages(self) -> list[Message]:
        return [m for m in self.messages if m.role == "assistant"]

    def tool_results(self) -> list[Message]:
        return [m for m in self.messages if m.role == "tool"]

    def system_message(self) -> Message | None:
        for m in self.messages:
            if m.role == "system":
                return m
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": [m.model_dump() for m in self.messages],
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentState:
        messages = [Message(**m) if isinstance(m, dict) else m for m in data.get("messages", [])]
        return cls(messages=messages, extra=dict(data.get("extra", {})))
