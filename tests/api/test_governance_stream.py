"""Issue #12 regression: governance guards must run on the streaming path.

A hard_block-tier tool called via a streamed loop must NOT execute; the
model receives a synthetic governance refusal instead.
"""

from __future__ import annotations

import asyncio

import pytest

from src.sdk.governance import GovernanceService
from src.sdk.middleware_hitl import HITLMiddleware


@pytest.fixture()
def gov_svc(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod

    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
    )
    import src.sdk.governance as gov

    monkeypatch.setattr(gov, "_services", {})
    monkeypatch.setattr(gov, "governance_enabled", lambda: True)
    return GovernanceService()


@pytest.mark.asyncio
async def test_hard_block_tool_never_executes_on_stream(gov_svc, monkeypatch):
    from src.sdk.loop import AgentLoop
    from src.sdk.messages import Message

    executed: list[str] = []

    import json as _json

    from src.sdk.messages import StreamChunk

    class Provider:
        def chat_stream(self, messages, tools=None, model=None, **kwargs):
            async def _stream():
                yield StreamChunk.tool_input_start(tool="gated_tool", call_id="call_1")
                yield StreamChunk.tool_input_delta(
                    call_id="call_1", content=_json.dumps({"x": "1"})
                )
                yield StreamChunk.tool_input_end(call_id="call_1", tool="gated_tool")
                yield StreamChunk.done(content="")

            return _stream()

    monkeypatch.setattr(
        "src.sdk.governance.get_governance_service", lambda user_id=None: gov_svc
    )
    # Tier via capabilities profile (plan M4-1 tier source).
    import src.sdk.capabilities as caps_mod

    monkeypatch.setattr(
        caps_mod,
        "load_capabilities",
        lambda root: {"governance_tiers": {"gated_tool": "hard_block"}},
    )

    from src.sdk.tools import tool

    @tool(name="gated_tool")
    async def gated_tool(x: str = "") -> str:  # pragma: no cover - must not run
        """Gated tool (hard_block tier)."""
        executed.append(x)
        return "EXECUTED"

    from src.sdk.middleware_hitl import HITLMiddleware

    loop = AgentLoop(
        provider=Provider(),
        tools=[gated_tool],
        user_id="stream_user",
        run_config=None,
        middlewares=[HITLMiddleware(user_id="stream_user")],
    )
    loop._flow_model = "test-model"

    chunks = [c async for c in loop.run_stream([Message.user("go")])]

    assert executed == []  # the tool body never ran
    previews = [str(getattr(c, "result_preview", "")) for c in chunks]
    assert any("governance" in p and "blocked" in p for p in previews)


class _StreamToolCallProvider:
    """Provider that proposes one tool call in the first streamed response,
    then a bare done (so exactly one tool dispatch occurs per run)."""

    def __init__(self, tool_name: str, arguments: dict | None = None) -> None:
        self._tool_name = tool_name
        self._arguments = dict(arguments or {})
        self._n = 0

    def chat_stream(self, messages, tools=None, model=None, **kwargs):
        import json as _json

        from src.sdk.messages import StreamChunk

        async def _stream():
            self._n += 1
            if self._n == 1:
                yield StreamChunk.tool_input_start(
                    tool=self._tool_name, call_id="call_1"
                )
                yield StreamChunk.tool_input_delta(
                    call_id="call_1", content=_json.dumps(self._arguments)
                )
                yield StreamChunk.tool_input_end(call_id="call_1", tool=self._tool_name)
            yield StreamChunk.done(content="")

        return _stream()


class _CountingHITL(HITLMiddleware):
    """HITLMiddleware that records every guard dispatch (issue #19)."""

    def __init__(self, user_id: str) -> None:
        super().__init__(user_id=user_id)
        self.guard_calls: list[str] = []

    async def guard_tool_call(self, tool_name, tool_input):  # type: ignore[override]
        self.guard_calls.append(tool_name)
        return await super().guard_tool_call(tool_name, tool_input)


class TestStreamBlockedCallTerminal:
    """Issue #19 discriminator (stream): a governance-blocked tool call is
    terminal on BOTH streaming executors, driven by the SSE consumer's
    per-step-task pattern (each __anext__ in a NEW asyncio task)."""

    @pytest.fixture()
    def gov_env(self, tmp_path, monkeypatch):
        import src.storage.paths as paths_mod
        from src.config import reload_settings

        monkeypatch.setattr(
            paths_mod.DataPaths,
            "root",
            property(lambda self: tmp_path / "root"),
            raising=False,
        )
        import src.sdk.governance as gov

        monkeypatch.setattr(gov, "_services", {})
        monkeypatch.setattr(gov, "_metering_lock_holder", None, raising=False)
        monkeypatch.setenv("GOVERNANCE_ENABLED", "true")
        monkeypatch.setenv("GOVERNANCE_TIERS", '{"explicit_tool": "explicit"}')
        reload_settings()
        yield
        monkeypatch.undo()
        paths_mod._paths_cache.clear()
        reload_settings()

    @staticmethod
    async def _consume_per_step_task(iterator):
        """Mimic the SSE consumer: one asyncio task per generator step."""
        chunks = []
        while True:
            try:
                chunks.append(await asyncio.ensure_future(iterator.__anext__()))
            except StopAsyncIteration:
                break
        return chunks

    @staticmethod
    def _make_loop(monkeypatch, gov_env, *, destructive: bool, mw):
        from src.sdk.loop import AgentLoop
        from src.sdk.tools import ToolAnnotations, ToolDefinition

        executions: list[str] = []

        async def body(**kwargs):
            executions.append("ran")
            return "EXECUTED-BODY"

        td = ToolDefinition(
            name="explicit_tool",
            description="gated tool",
            function=body,
            annotations=ToolAnnotations(destructive=destructive),
        )
        loop = AgentLoop(
            provider=_StreamToolCallProvider("explicit_tool"),
            tools=[td],
            user_id="stream_term_user",
            run_config=None,
            middlewares=[mw],
        )
        loop._flow_model = "test-model"
        return loop, executions

    @pytest.mark.asyncio
    async def test_batch_stream_blocked_call_is_terminal(
        self, gov_env, monkeypatch
    ):

        from src.sdk.messages import Message

        mw = _CountingHITL(user_id="stream_term_user")
        loop, executions = self._make_loop(
            monkeypatch, gov_env, destructive=False, mw=mw
        )
        chunks = await self._consume_per_step_task(
            loop.run_stream([Message.user("run it")])
        )

        assert executions == []
        assert mw.guard_calls == ["explicit_tool"]
        tool_results = [c for c in chunks if c.canonical_type == "tool_result"]
        assert len(tool_results) == 1
        assert "awaiting explicit human approval" in tool_results[0].result_preview
        assert loop.state.extra.get("_executed_tool_calls", []) == []
        tool_msgs = [m for m in loop.state.messages if m.role == "tool"]
        assert len(tool_msgs) == 1

    @pytest.mark.asyncio
    async def test_sequential_stream_blocked_call_is_terminal(
        self, gov_env, monkeypatch
    ):

        from src.sdk.messages import Message

        mw = _CountingHITL(user_id="stream_term_user")
        loop, executions = self._make_loop(
            monkeypatch, gov_env, destructive=True, mw=mw
        )
        chunks = await self._consume_per_step_task(
            loop.run_stream([Message.user("run it")])
        )

        assert executions == []
        assert mw.guard_calls == ["explicit_tool"]
        tool_results = [c for c in chunks if c.canonical_type == "tool_result"]
        assert len(tool_results) == 1
        assert "awaiting explicit human approval" in tool_results[0].result_preview
        assert loop.state.extra.get("_executed_tool_calls", []) == []
        tool_msgs = [m for m in loop.state.messages if m.role == "tool"]
        assert len(tool_msgs) == 1
