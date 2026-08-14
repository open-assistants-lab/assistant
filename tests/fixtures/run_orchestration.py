"""Scripted orchestration fixture for transport parity tests.

Provides deterministic UUID, clock, token-estimator, provider, event-sink,
and filesystem boundaries. One script drives REST, SSE, and WebSocket parity
assertions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.sdk.messages import Message, StreamChunk


class DeterministicUUID:
    """Deterministic UUID generator for reproducible test runs."""

    def __init__(self) -> None:
        self._counter = 0

    def uuid4(self) -> uuid.UUID:
        self._counter += 1
        return uuid.UUID(f"00000000-0000-4000-8000-{self._counter:012x}")

    def __str__(self) -> str:
        return str(self.uuid4())


class DeterministicClock:
    """Deterministic clock for reproducible timestamps."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        result = self._now
        self._now += timedelta(seconds=1)
        return result


class ScriptedProvider:
    """Mock provider that returns scripted responses."""

    def __init__(self, responses: list[Message]) -> None:
        self._responses = responses
        self._index = 0
        self.model_id = "test:scripted"
        self.provider_id = "test"

    async def chat(self, messages, tools=None, model=None, provider_options=None, **kwargs):
        if self._index < len(self._responses):
            response = self._responses[self._index]
            self._index += 1
            return response
        return Message.assistant(content="Scripted default response")

    async def chat_stream(self, messages, **kwargs):
        if self._index < len(self._responses):
            response = self._responses[self._index]
            self._index += 1
            yield StreamChunk(type="text_delta", content=response.content)
            yield StreamChunk(type="done", content=response.content)
        else:
            yield StreamChunk(type="text_delta", content="Scripted default response")
            yield StreamChunk(type="done", content="Scripted default response")

    async def list_models(self):
        return [{"id": "test:scripted", "name": "Test Scripted Model"}]


class InMemoryEventCollector:
    """Collects events for later assertion."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def add(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def clear(self) -> None:
        self.events.clear()

    @property
    def types(self) -> list[str]:
        return [e.get("type", "") for e in self.events]

    def by_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == event_type]


@pytest.fixture
def orchestration_fixture():
    """Returns a fixture with deterministic UUID, clock, token-estimator, provider,
    event-sink, and filesystem boundaries. One script drives all three transports."""
    det_uuid = DeterministicUUID()
    det_clock = DeterministicClock()
    provider = ScriptedProvider([
        Message.assistant(content="Hello! I can help with that."),
    ])
    collector = InMemoryEventCollector()

    return {
        "uuid": det_uuid,
        "clock": det_clock,
        "provider": provider,
        "collector": collector,
    }
