"""Execution and prompt hard-ceiling regressions for #16."""

from types import SimpleNamespace

import pytest

from src.sdk.loop import AgentLoop
from src.sdk.messages import ToolCall
from src.sdk.tools import ToolDefinition


def _tool(name: str, function=None) -> ToolDefinition:
    return ToolDefinition(name=name, description=name, parameters={}, function=function)


class _Index:
    def __init__(self, definition: ToolDefinition, tool_type: str) -> None:
        self.definition = definition
        self.tool_type = tool_type

    def get_definition(self, name: str) -> ToolDefinition:
        assert name == self.definition.name
        return self.definition

    def get_reconstruct(self, name: str) -> dict[str, str]:
        return {}

    def get_tool_type(self, name: str) -> str:
        return self.tool_type


@pytest.mark.asyncio
async def test_lazy_disabled_native_is_rejected_before_global_lookup(monkeypatch):
    loop = AgentLoop(provider=object(), tools=[], caps_check=lambda name: True)
    loop._tool_index = _Index(_tool("files_read"), "native")
    loop._native_tool_deployment_disabled = lambda name: name == "files_read"
    monkeypatch.setattr(
        "src.sdk.native_tools.get_native_tools",
        lambda: pytest.fail("disabled native must not consult global registry"),
    )

    result = await loop._try_lazy_load(ToolCall(id="1", name="files_read", arguments={}))

    assert result is not None
    assert result.is_error
    assert result.content == "Tool is disabled: files_read"
    assert "files_read" not in loop._registry.list_names()


@pytest.mark.asyncio
async def test_lazy_custom_matching_native_glob_remains_eligible(monkeypatch):
    async def custom() -> str:
        return "custom-result"

    custom_tool = _tool("files_read", custom)
    loop = AgentLoop(provider=object(), tools=[], caps_check=lambda name: True)
    loop._tool_index = _Index(custom_tool, "custom")
    loop._native_tool_deployment_disabled = lambda name: True
    monkeypatch.setattr("src.sdk.tool_index._rebuild_custom_function", lambda td, reconstruct: td)

    result = await loop._try_lazy_load(ToolCall(id="1", name="files_read", arguments={}))

    assert result is not None
    assert not result.is_error
    assert result.content == "custom-result"


def test_deployment_disabled_native_tools_are_omitted_from_prompt_preferences():
    from src.sdk.runner import _build_tool_preferences, _get_memory_context

    settings = SimpleNamespace(tools=SimpleNamespace(disabled=["files_*", "message_search"]))
    caps = {"tools": {}}
    assert "files_read" not in _build_tool_preferences(caps, settings)
    assert "message_search" not in _get_memory_context(caps, settings)
