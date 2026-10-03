"""Lazy-loaded executions must appear in the audit trail (#59)."""
import tempfile
from pathlib import Path

import pytest

from src.sdk.loop import AgentLoop, RunConfig
from src.sdk.messages import ToolCall
from src.sdk.tools import ToolDefinition


@pytest.mark.asyncio
async def test_lazy_loaded_tool_emits_audit_events(monkeypatch):
    from src.sdk.tool_index import ToolIndex, _rebuild_custom_function

    index = ToolIndex(Path(tempfile.mkdtemp()) / "idx")
    try:
        async def body(path: str) -> str:
            return f"ran {path}"

        td = ToolDefinition(
            name="greeter",
            description="Lazy",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        reconstruct = {"command": "echo hi", "install": []}
        td = _rebuild_custom_function(td, reconstruct)
        index.index_tool(td, tool_type="custom", reconstruct=reconstruct)

        events: list = []
        loop = AgentLoop(provider=object(), tools=[], run_config=RunConfig())
        loop._tool_index = index
        loop.capture_bus.subscribe(lambda e: events.append(e))
        await loop._execute_tool(ToolCall(id="c1", name="greeter", arguments={"path": "x"}))
        kinds = [(e.kind, e.tool) for e in events]
        assert ("tool_call", "greeter") in kinds, kinds
        assert ("tool_result", "greeter") in kinds, kinds
    finally:
        index.close()
