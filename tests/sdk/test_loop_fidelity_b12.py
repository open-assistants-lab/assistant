"""B12: loop/transcript fidelity — classification, cancel, ordering, receipts (#70-#79, #118, #126)."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.sdk.loop import AgentLoop
from src.sdk.messages import Message, StreamChunk, ToolCall
from src.sdk.tools import ToolAnnotations, tool
from tests.sdk.test_sdk_loop import MockProvider


@tool
async def reader(path: str = "") -> str:
    """Read-only."""
    return f"contents of {path}"


reader.annotations = ToolAnnotations(title="Read", read_only=True, idempotent=True)


class TestUnknownToolsAreSequential:
    """#70: an unresolved definition must not be classified parallel-safe."""

    def test_is_parallel_safe_is_false_for_an_unknown_tool(self):
        loop = AgentLoop(provider=MockProvider(), tools=[])
        assert loop._is_parallel_safe(ToolCall(id="a", name="not-registered", arguments={})) is False

    def test_classification_puts_unknown_tools_in_the_sequential_group(self):
        loop = AgentLoop(provider=MockProvider(), tools=[reader])
        parallel, sequential, interrupts = loop._classify_tool_calls([
            ToolCall(id="r", name="reader", arguments={}),
            ToolCall(id="x", name="lazy-tool", arguments={}),
        ])
        assert [t.id for t in parallel] == ["r"]
        assert [t.id for t in sequential] == ["x"], (
            "an unresolved (lazy) tool was dispatched concurrently"
        )


class TestNonstreamCancellation:
    """#71: AgentLoop.run must honour its cancel_event."""

    @pytest.mark.asyncio
    async def test_precancelled_run_makes_no_model_calls_and_runs_no_tools(self):
        ran: list[str] = []

        @tool
        async def marker(text: str = "x") -> str:
            """Writes."""
            ran.append(text)
            return "wrote"

        provider = MockProvider(responses=[
            Message.assistant(tool_calls=[ToolCall(id="w", name="marker", arguments={"text": "a"})]),
            Message.assistant(content="done"),
        ])
        cancel = asyncio.Event()
        cancel.set()
        loop = AgentLoop(provider=provider, tools=[marker], cancel_event=cancel)
        result = await loop.run([Message.user("go")])
        assert ran == [], f"a cancelled run executed a tool: {ran}"
        assert provider._call_count == 0, (
            f"a cancelled run called the model {provider._call_count} times"
        )
        assert any("cancel" in str(m.content).lower() for m in result), result


class TestMixedBatchAnswersEachIdOnce:
    """#72: the nonstream repetition branch answered duplicates twice."""

    @pytest.mark.asyncio
    async def test_each_proposed_id_gets_exactly_one_result(self):
        fresh_seen: list[int] = []

        @tool
        async def fresh(n: int = 0) -> str:
            """Records n."""
            fresh_seen.append(n)
            return f"fresh {n}"

        fresh.annotations = ToolAnnotations(title="Fresh", read_only=True, idempotent=True)

        provider = MockProvider(responses=[
            Message.assistant(tool_calls=[ToolCall(id="r0", name="reader", arguments={})]),
            Message.assistant(tool_calls=[
                ToolCall(id="r1", name="reader", arguments={}),
                ToolCall(id="f1", name="fresh", arguments={"n": 1}),
            ]),
            Message.assistant(tool_calls=[
                ToolCall(id="r2", name="reader", arguments={}),
                ToolCall(id="f2", name="fresh", arguments={"n": 2}),
            ]),
            Message.assistant(tool_calls=[
                ToolCall(id="r3", name="reader", arguments={}),
                ToolCall(id="f3", name="fresh", arguments={"n": 3}),
            ]),
            Message.assistant(content="done"),
        ])
        loop = AgentLoop(provider=provider, tools=[reader, fresh])
        result = await loop.run([Message.user("go")])

        counts: dict[str, int] = {}
        for m in result:
            if m.role == "tool" and m.tool_call_id:
                counts[m.tool_call_id] = counts.get(m.tool_call_id, 0) + 1
        doubled = {k: v for k, v in counts.items() if v > 1}
        assert not doubled, f"tool ids answered more than once: {doubled}"


class TestDefinitionBinding:
    """#73: helper/list captures differentiate bodies; the workspace is carried."""

    def test_list_captures_change_the_fingerprint(self):
        from src.sdk.governance import definition_fingerprint
        from src.sdk.tools import ToolDefinition

        def make(value):
            async def body():
                return value[0]

            return ToolDefinition(
                name="probe", description="probe",
                parameters={"type": "object", "properties": {}}, function=body,
            )

        a, b = make(["ORIGINAL"]), make(["REPLACEMENT"])
        assert definition_fingerprint(a) != definition_fingerprint(b), (
            "a list capture produced an identical fingerprint"
        )

    def test_helper_callables_change_the_fingerprint(self):
        from src.sdk.governance import definition_fingerprint
        from src.sdk.tools import ToolDefinition

        def helper_one():
            return "one"

        def helper_two():
            return "two"

        def make(helper):
            async def body():
                return helper()

            return ToolDefinition(
                name="probe", description="probe",
                parameters={"type": "object", "properties": {}}, function=body,
            )

        assert definition_fingerprint(make(helper_one)) != definition_fingerprint(make(helper_two))

    @pytest.mark.asyncio
    async def test_the_proposal_records_its_workspace(self, tmp_path, monkeypatch):
        import src.sdk.governance as gov
        import src.storage.paths as paths_mod
        from src.sdk.governance import GovernanceService

        monkeypatch.setattr(
            paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
        )
        monkeypatch.setattr(gov, "_services", {})
        svc = GovernanceService()
        pid = svc.create_pending(
            "alice", "probe", {}, permission="ask", workspace_id="project"
        )
        assert svc.get_pending("alice", pid)["workspace_id"] == "project"


class TestStreamCancelFinalizes:
    """#75: cancellation must answer every pending id and run after_agent."""

    @pytest.mark.asyncio
    async def test_cancel_between_sequential_tools_answers_and_cleans_up(self):
        cleaned: list[str] = []

        class _Cleanup:
            name = "cleanup"

            async def aafter_agent(self, state):
                cleaned.append("cleanup")

        ran: list[str] = []
        loop_box: dict = {}

        @tool
        async def first(text: str = "x") -> str:
            """Runs then cancels."""
            ran.append("first")
            loop_box["loop"].cancel_event.set()
            return "wrote"

        @tool
        async def second(text: str = "x") -> str:
            """Must not run."""
            ran.append("second")
            return "wrote"

        first.annotations = ToolAnnotations(destructive=True, read_only=False)
        second.annotations = ToolAnnotations(destructive=True, read_only=False)

        provider = MockProvider()
        provider.set_stream_events([
            [
                StreamChunk.tool_input_start(tool="first", call_id="a"),
                StreamChunk.tool_input_delta(call_id="a", content="{}"),
                StreamChunk.tool_input_end(tool="first", call_id="a"),
                StreamChunk.tool_input_start(tool="second", call_id="b"),
                StreamChunk.tool_input_delta(call_id="b", content="{}"),
                StreamChunk.tool_input_end(tool="second", call_id="b"),
                StreamChunk.done(content=""),
            ],
        ])
        loop = AgentLoop(provider=provider, tools=[first, second], middlewares=[_Cleanup()])
        loop_box["loop"] = loop
        async for _ in loop.run_stream([Message.user("go")]):
            pass

        proposed = [tc.id for m in loop.state.messages if m.role == "assistant" for tc in (m.tool_calls or [])]
        answered = [m.tool_call_id for m in loop.state.messages if m.role == "tool" and m.tool_call_id]
        assert sorted(proposed) == sorted(answered), (
            f"unanswered after cancel: {sorted(set(proposed) - set(answered))}"
        )
        assert cleaned == ["cleanup"], "after_agent did not run on the cancelled stream"


class TestSteerOrdering:
    """#76: cancelled results must precede the steer user message."""

    @pytest.mark.asyncio
    async def test_cancelled_results_come_before_the_steer(self):
        loop_box: dict = {}

        @tool
        async def destructive_a(text: str = "x") -> str:
            """Runs then steers."""
            loop_box["loop"].steer("change plan")
            return "wrote"

        @tool
        async def destructive_b(text: str = "x") -> str:
            """Skipped by the steer."""
            return "wrote"

        destructive_a.annotations = ToolAnnotations(destructive=True, read_only=False)
        destructive_b.annotations = ToolAnnotations(destructive=True, read_only=False)

        provider = MockProvider(responses=[
            Message.assistant(tool_calls=[
                ToolCall(id="a", name="destructive_a", arguments={}),
                ToolCall(id="b", name="destructive_b", arguments={}),
            ]),
            Message.assistant(content="done"),
        ])
        loop = AgentLoop(provider=provider, tools=[destructive_a, destructive_b])
        loop_box["loop"] = loop
        result = await loop.run([Message.user("go")])

        kinds: list[str] = []
        for m in result:
            if m.role == "tool" and m.tool_call_id == "b":
                kinds.append("result-b")
            elif m.role == "user" and m.content == "change plan":
                kinds.append("steer")
        assert kinds == ["result-b", "steer"], (
            f"steer interleaved before the pending result: {kinds}"
        )


class TestDuplicateReceiptWithInjectedContext:
    """#77: the receipt must quote the real prior result."""

    @pytest.mark.asyncio
    async def test_receipt_quotes_the_prior_result(self):
        @tool
        async def contextual(user_id: str = "", path: str = "") -> str:
            """Read with a runtime-injected user_id."""
            return "RESULT_WITH_CONTEXT"

        contextual.annotations = ToolAnnotations(title="Ctx", read_only=True, idempotent=True)
        provider = MockProvider(responses=[
            Message.assistant(tool_calls=[ToolCall(id="a", name="contextual", arguments={})]),
            Message.assistant(tool_calls=[ToolCall(id="b", name="contextual", arguments={})]),
            Message.assistant(content="done"),
        ])
        loop = AgentLoop(provider=provider, tools=[contextual], user_id="test-user")
        result = await loop.run([Message.user("go")])
        synthetic = [
            str(m.content) for m in result
            if m.role == "tool" and str(m.content).startswith("Duplicate call skipped")
        ]
        assert synthetic, "the duplicate was not answered"
        assert "RESULT_WITH_CONTEXT" in synthetic[0], synthetic[0]


class TestSessionHeaderPrompt:
    """#78: the header logs the CURRENT prompt (last user message)."""

    def test_header_uses_the_latest_user_message(self, monkeypatch):
        import src.sdk.session_events as se

        logged: list[dict] = []
        monkeypatch.setattr(se, "session_log_enabled", lambda: True)

        class _Store:
            def append(self, event):
                data = getattr(event, "data", None)
                content = getattr(data, "content", None) if data is not None else None
                logged.append({"type": event.type, "content": content or ""})

        monkeypatch.setattr(se, "get_session_event_store", lambda user: _Store())
        loop = AgentLoop(provider=MockProvider(), tools=[])
        loop._log_session_header([
            Message.user("FIRST"),
            Message.assistant(content="A1"),
            Message.user("SECOND"),
        ])
        prompts = [e["content"] for e in logged if e["type"] == "user_prompt"]
        assert prompts == ["SECOND"], f"the header logged the wrong prompt: {prompts}"


class TestStartArgsAreHonored:
    """#79: complete args on tool_input_start must reach the tool."""

    @pytest.mark.asyncio
    async def test_start_only_args_reach_the_tool(self):
        seen: list[str] = []

        @tool
        async def with_default(value: str = "DEFAULT") -> str:
            """Takes a value."""
            seen.append(value)
            return f"got {value}"

        class _Provider(MockProvider):
            async def chat_stream_impl(self, messages, tools=None, model=None, **kwargs):
                if self._call_count == 0:
                    self._call_count += 1
                    yield StreamChunk.tool_input_start(
                        tool="with_default", call_id="a", args={"value": "EXPECTED"}
                    )
                    yield StreamChunk.tool_input_end(tool="with_default", call_id="a")
                    yield StreamChunk.done(content="")
                else:
                    self._call_count += 1
                    yield StreamChunk.text_delta(content="ok")
                    yield StreamChunk.done(content="ok")

            def chat_stream(self, messages, tools=None, model=None, **kwargs):
                return self.chat_stream_impl(messages, tools, model, **kwargs)

        loop = AgentLoop(provider=_Provider(), tools=[with_default])
        async for _ in loop.run_stream([Message.user("go")]):
            pass
        assert seen == ["EXPECTED"], f"start args were dropped: {seen}"


class TestSandboxTimeoutKillsDescendants:
    """#118: a timed-out command must not leave descendants running."""

    def test_descendant_writer_is_stopped(self, tmp_path):
        from src.sdk.sandbox import SandboxLimits, SoftSandboxBackend

        marker = tmp_path / "late.txt"
        backend = SoftSandboxBackend()
        script = (
            f"(sleep 3; echo late > {marker}) & sleep 30"
        )
        result = backend.run(
            ["/bin/sh", "-c", script],
            cwd=tmp_path,
            limits=SandboxLimits(timeout_seconds=1.0),
        )
        assert result.timed_out, result
        deadline = time.time() + 5
        while time.time() < deadline and marker.exists():
            time.sleep(0.1)
        assert not marker.exists(), "a descendant survived the sandbox timeout"
