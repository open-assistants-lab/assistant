"""tool_search results must become callable on the next model turn."""

from __future__ import annotations

from types import SimpleNamespace

from src.sdk.loop import AgentLoop
from src.sdk.tools import ToolDefinition
from src.sdk.tools_core import tool_search as tool_search_module


class _Index:
    def __init__(self, definition: ToolDefinition) -> None:
        self.definition = definition

    def count(self) -> int:
        return 1

    def search_with_tool_types(self, description: str, limit: int):
        return [("summarize_session", "Summarize the current conversation", "native")]

    def get_definition(self, name: str):
        return self.definition if name == "summarize_session" else None

    def get_reconstruct(self, name: str):
        return {}

    def get_tool_type(self, name: str):
        return "native" if name == "summarize_session" else None


def test_tool_search_loads_discovered_native_tool_for_next_turn(monkeypatch):
    definition = ToolDefinition(
        name="summarize_session",
        description="Summarize the current conversation",
        function=lambda: "summarized",
    )
    loop = AgentLoop(
        provider=SimpleNamespace(provider_type="test", model="test"),
        tools=[tool_search_module.tool_search],
    )
    loop._tool_index = _Index(definition)
    monkeypatch.setattr(tool_search_module, "get_current_agent_loop", lambda: loop)
    monkeypatch.setattr(tool_search_module, "load_user_capabilities", lambda _user_id: {})

    result = tool_search_module.tool_search.invoke(
        {"description": "summarize the conversation", "user_id": "user"}
    )

    assert "summarize_session" in result
    assert loop._registry.get("summarize_session") is not None
