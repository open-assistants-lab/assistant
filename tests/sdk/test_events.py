"""Tests for TriggerRegistry and AgentEvent."""

import pytest
from src.sdk.loops.events import AgentEvent, TriggerRegistry


def test_agent_event_fields():
    event = AgentEvent(
        trigger_type="webhook",
        trigger_id="wh_123",
        user_id="alice",
        session_id="s1",
        message="check my email",
    )
    assert event.trigger_type == "webhook"
    assert event.rubric is None
    assert event.metadata == {}


@pytest.mark.asyncio
async def test_trigger_registry_fires_registered_handler():
    registry = TriggerRegistry()
    fired = []

    async def handler(event: AgentEvent):
        fired.append(event)

    registry.register("webhook", handler)

    event = AgentEvent(
        trigger_type="webhook",
        trigger_id="wh_1",
        user_id="alice",
        session_id="s1",
        message="hello",
    )
    await registry.fire(event)

    assert len(fired) == 1
    assert fired[0].message == "hello"


@pytest.mark.asyncio
async def test_trigger_registry_unknown_type_raises():
    registry = TriggerRegistry()
    event = AgentEvent(
        trigger_type="unknown",
        trigger_id="x",
        user_id="alice",
        session_id="s1",
        message="hello",
    )
    with pytest.raises(KeyError):
        await registry.fire(event)


@pytest.mark.asyncio
async def test_trigger_registry_unregister():
    registry = TriggerRegistry()
    fired = []

    async def handler(event: AgentEvent):
        fired.append(event)

    registry.register("webhook", handler)
    registry.unregister("webhook")

    event = AgentEvent(
        trigger_type="webhook",
        trigger_id="wh_1",
        user_id="alice",
        session_id="s1",
        message="hello",
    )
    with pytest.raises(KeyError):
        await registry.fire(event)