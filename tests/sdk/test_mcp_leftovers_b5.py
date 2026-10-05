"""B5: MCP leftover defects — annotation-object cache failure (#103), non-idempotent replay (#107)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.sdk.tools_core.mcp_bridge import MCPToolBridge
from src.sdk.tools_core.mcp_cache import MCPToolMetadataCache
from src.sdk.tools_core.mcp_manager import MCPManager


class _Conn:
    def __init__(self, tools):
        self.tools = tools


def _mcp_tool_with_annotations() -> SimpleNamespace:
    """A tool exactly as the real mcp SDK exposes it: annotations as a pydantic object."""
    from mcp.types import ToolAnnotations

    return SimpleNamespace(
        name="echo",
        description="",
        inputSchema={"type": "object", "properties": {}},
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
    )


class TestAnnotationObjectsSurviveCaching:
    """#103: a server whose tools declare annotations never connected because
    the cache write crashed on a pydantic object and the except path closed
    the candidate while leaving it registered."""

    def test_cache_write_survives_pydantic_annotations(self, tmp_path):
        cache = MCPToolMetadataCache(tmp_path / "cache.json")
        manager = MCPManager("cache-user")
        try:
            manager._cache = cache
            conn = _Conn([_mcp_tool_with_annotations()])
            manager._cache_server_metadata("fixture", conn)
            record = cache.get("fixture")
            assert record is not None, "the cache write failed"
            tool = record["tools"][0]
            assert isinstance(tool["annotations"], dict)
            assert tool["annotations"].get("readOnlyHint") is True
        finally:
            import asyncio

            asyncio.run(manager.cleanup())

    @pytest.mark.asyncio
    async def test_a_failed_cache_write_does_not_close_a_registered_connection(
        self, tmp_path, monkeypatch
    ):
        """Even when the cache write raises, a started connection must survive."""
        manager = MCPManager("cache-user")
        try:
            conn = _Conn([_mcp_tool_with_annotations()])
            manager._connections["fixture"] = conn

            def broken_put(record):
                raise OSError("disk full")

            monkeypatch.setattr(manager._cache, "put", broken_put)
            with pytest.raises(OSError):
                manager._cache_server_metadata("fixture", conn)
            # The connection is untouched (registered before caching).
            assert (await manager.snapshot_connections()).get("fixture") is conn
        finally:
            await manager.cleanup()


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


class _FakeSession:
    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.attempts = 0

    async def call_tool(self, name, arguments):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise TimeoutError("the request timed out")
        return SimpleNamespace(content=[SimpleNamespace(text="ok")], isError=False)


class TestNonIdempotentCallsAreNotReplayed:
    """#107: a timeout/transport failure must not re-send a call that may
    already have executed server-side."""

    @staticmethod
    def _bridge_for(annotations) -> MCPToolBridge:
        bridge = MCPToolBridge("alice")
        tool = SimpleNamespace(
            name="send",
            description="",
            inputSchema={"type": "object", "properties": {}},
            annotations=annotations,
        )
        definition = bridge._convert_mcp_tool("mcp__srv__send", tool, "srv")
        bridge._manager = SimpleNamespace(
            _ensure_connection=None, _last_errors={}
        )
        return bridge, definition

    @pytest.mark.asyncio
    async def test_a_non_idempotent_tool_is_called_once_on_failure(self, monkeypatch):
        from mcp.types import ToolAnnotations

        bridge, definition = self._bridge_for(
            ToolAnnotations(destructiveHint=True)  # not idempotent
        )
        session = _FakeSession(fail_times=99)

        async def ensure(server_name, server=None, *, force_reconnect=False):
            return SimpleNamespace(session=session)

        monkeypatch.setattr(
            bridge, "_ensure_connection", ensure
        )
        result = await definition.ainvoke({})
        assert result.is_error
        assert session.attempts == 1, (
            f"a possibly-executed call was replayed {session.attempts}x"
        )

    @pytest.mark.asyncio
    async def test_an_idempotent_tool_may_retry_after_a_transport_failure(self, monkeypatch):
        from mcp.types import ToolAnnotations

        bridge, definition = self._bridge_for(
            ToolAnnotations(idempotentHint=True)
        )
        session = _FakeSession(fail_times=1)

        async def ensure(server_name, server=None, *, force_reconnect=False):
            return SimpleNamespace(session=session)

        monkeypatch.setattr(bridge, "_ensure_connection", ensure)
        result = await definition.ainvoke({})
        assert result.is_error is False
        assert session.attempts == 2, "an idempotent call gave up on a transient failure"
