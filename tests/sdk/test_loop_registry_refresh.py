from __future__ import annotations

from typing import Any

import pytest

from src.sdk.loop import AgentLoop
from src.sdk.messages import Message
from src.sdk.providers.base import LLMProvider, ModelInfo
from src.sdk.tools import ToolDefinition


class RecordingProvider(LLMProvider):
    def __init__(self) -> None:
        self.tool_sets: list[set[str]] = []

    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.tool_sets.append({tool.name for tool in tools or []})
        return Message.assistant(content="done")

    def chat_stream(self, messages, tools=None, model=None, **kwargs):
        raise NotImplementedError

    def count_tokens(self, text: str, model: str | None = None) -> int:
        return 1

    def get_model_info(self, model: str) -> ModelInfo:
        return ModelInfo(id=model, name=model, provider_id="test")

    @property
    def provider_id(self) -> str:
        return "test"


def _td(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        function=lambda: name,
    )


@pytest.mark.asyncio
async def test_register_and_unregister_are_visible_on_the_next_chat() -> None:
    provider = RecordingProvider()
    loop = AgentLoop(provider, tools=[_td("one"), _td("two")])

    await loop.run([Message.user("before")])
    loop.register_tool(_td("three"))
    await loop.run([Message.user("after register")])
    loop.unregister_tool("three")
    await loop.run([Message.user("after unregister")])

    assert provider.tool_sets == [{"one", "two"}, {"one", "two", "three"}, {"one", "two"}]


def test_capability_toggle_refreshes_all_live_user_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.sdk.capabilities as capabilities
    import src.sdk.runner as runner

    loop = AgentLoop(RecordingProvider(), tools=[_td("always"), _td("toggle")], user_id="alice")
    runner._loop_cache["alice:model:test:session:one"] = loop
    saved: dict[str, Any] = {"tools": {}, "skills": {}, "subagents": {}}
    monkeypatch.setattr(capabilities, "load_user_capabilities", lambda user_id: saved)
    monkeypatch.setattr(capabilities, "save_user_capabilities", lambda user_id, caps: None)
    monkeypatch.setattr(runner, "_load_user_capabilities", lambda user_id: saved)
    monkeypatch.setattr(runner, "_current_tool_catalog", lambda live_loop: [_td("always"), _td("toggle")])

    capabilities.set_resource_enabled("alice", "tools", "toggle", False)
    assert loop._registry.list_names() == ["always"]
    capabilities.set_resource_enabled("alice", "tools", "toggle", True)
    assert loop._registry.list_names() == ["always", "toggle"]

    runner._loop_cache.clear()
