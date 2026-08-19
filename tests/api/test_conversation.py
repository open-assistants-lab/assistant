"""Contract tests for conversation endpoints."""

import pytest


def _make_run_event_factory(chunk_gen):
    """Build a fake `RunService.execute_stream` that replays StreamChunks as RunEvents.

    The conversation router's message/stream endpoint now consumes RunEvents from
    RunService.execute_stream (not StreamChunks from run_sdk_agent_stream). The legacy
    tests below were written against StreamChunk generators, so this adapter bridges
    them: it normalizes each chunk via adapt_stream_chunk and emits the matching
    canonical RunEvent, preserving ordering and the dedup/cancel semantics the tests
    rely on.
    """
    from datetime import UTC, datetime

    from src.http.stream_adapter import adapt_stream_chunk
    from src.sdk.run_events import (
        BlockDeltaData,
        ErrorData,
        ErrorEvent,
        InterruptData,
        InterruptEvent,
        ReasoningDeltaEvent,
        TextDeltaEvent,
        ToolInputStartEvent,
        ToolResultData,
        ToolResultEvent,
        ToolStartData,
    )

    _common = dict(
        event_id="e1",
        sequence=1,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        session_id="default",
        run_id="r1",
        attempt=1,
    )

    async def fake_execute_stream(self, *, session_id=None, prompt=None, model=None, provider_keys=None, **kwargs):
        async for chunk in chunk_gen():
            ev = adapt_stream_chunk(chunk)
            kind = ev.kind
            if kind == "text_delta" and ev.content:
                yield TextDeltaEvent(data=BlockDeltaData(block_id="b", delta=ev.content), **_common)
            elif kind == "reasoning_delta" and ev.content:
                yield ReasoningDeltaEvent(data=BlockDeltaData(block_id="b", delta=ev.content), **_common)
            elif kind == "tool_input_start" and ev.tool:
                yield ToolInputStartEvent(
                    data=ToolStartData(block_id="b", tool_call_id=ev.call_id or "c", name=ev.tool),
                    **_common,
                )
            elif kind == "tool_result" and ev.tool:
                content = ev.result_preview or ev.content or ""
                yield ToolResultEvent(
                    data=ToolResultData(
                        block_id="b",
                        tool_call_id=ev.call_id or "c",
                        name=ev.tool,
                        status="completed",
                        content=content,
                    ),
                    **_common,
                )
            elif kind == "interrupt":
                yield InterruptEvent(
                    data=InterruptData(tool=ev.tool or "t", call_id=ev.call_id or "c", args=ev.args or {}),
                    **_common,
                )
            elif kind == "error":
                yield ErrorEvent(
                    data=ErrorData(code="error", message=ev.content or "error", retryable=False),
                    **_common,
                )
            # Other kinds (text_start, reasoning_start, done, etc.) are dropped, mirroring
            # the router which only forwards the canonical content events these tests assert on.

    return fake_execute_stream


class FakeConversation:
    def __init__(self):
        self.messages = []

    def add_message(self, *args, **kwargs):
        self.messages.append((args, kwargs))
        return "msg-1"

    def get_messages_with_summary(self, *args, **kwargs):
        return []


class TestToolMessagePersistence:
    def test_persist_tool_messages_uses_session_id(self):
        from src.http.routers.conversation import _persist_tool_messages

        calls = []

        class FakeConversation:
            def add_message(self, *args, **kwargs):
                calls.append((args, kwargs))

        _persist_tool_messages(
            FakeConversation(),
            [{"tool": "message_search", "stage": "end", "call_id": "call-1", "output": "found"}],
            session_id="chat-1",
        )

        assert calls == [
            (
                ("tool", "found"),
                {
                    "metadata": {"tool_name": "message_search", "tool_call_id": "call-1"},
                    "session_id": "chat-1",
                },
            )
        ]


class TestMessageSessionPropagation:
    @pytest.mark.skip(
        reason="handle_message's verbose flag no longer selects a separate stream runner; "
        "session_id propagation to the runner is covered by "
        "TestStreamEdgeCases.test_non_verbose_message_passes_session_id_to_runner."
    )
    @pytest.mark.asyncio
    async def test_verbose_message_passes_session_id_to_stream_runner(self, monkeypatch):
        from src.http.models import MessageRequest
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        captured = {}

        class FakeConversation:
            def add_message(self, *args, **kwargs):
                return "msg-1"

            def get_messages_with_summary(self, *args, **kwargs):
                return []

        async def fake_run_sdk_agent_stream(**kwargs):
            captured.update(kwargs)
            yield StreamChunk.done()

        monkeypatch.setattr(conversation_router, "get_message_store", lambda user_id: FakeConversation())
        monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_run_sdk_agent_stream)

        await conversation_router.handle_message(
            MessageRequest(message="hello", user_id="test_user", session_id="chat-1", verbose=True),
            None,
        )

        assert captured["session_id"] == "chat-1"

    @pytest.mark.asyncio
    async def test_cancel_without_session_id_resets_default_session_only(self, monkeypatch):
        from src.http.routers import conversation as conversation_router

        calls = []

        def fake_reset_sdk_loop(user_id, workspace_id="personal", session_id=None):
            calls.append((user_id, workspace_id, session_id))

        monkeypatch.setattr(conversation_router, "reset_sdk_loop", fake_reset_sdk_loop)

        result = await conversation_router.cancel_message(conversation_router.CancelRequest(user_id="u"))

        assert result == {"status": "cancelled"}
        assert calls == [("u", "personal", "default")]


class TestTitleGeneration:
    @pytest.mark.asyncio
    async def test_summarize_title_passes_user_id_to_provider_factory(self, monkeypatch):
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import Message

        captured = {}

        class FakeProvider:
            async def chat(self, **kwargs):
                return Message.assistant("Project Planning")

        def fake_create_model_from_config(model, **kwargs):
            captured["model"] = model
            captured.update(kwargs)
            return FakeProvider()

        monkeypatch.setattr(
            "src.sdk.providers.factory.create_model_from_config",
            fake_create_model_from_config,
        )

        title = await conversation_router._summarize_title(
            "Plan the project", "Here is the plan", user_id="title_user"
        )

        assert title == "Project Planning"
        assert captured["user_id"] == "title_user"

    @pytest.mark.asyncio
    async def test_title_generation_keeps_raw_transcript_history(self, monkeypatch):
        from types import SimpleNamespace

        from src.http.routers import conversation as conversation_router

        calls = []

        class Store:
            def get_session_title(self, session_id):
                return None

            def get_sessions(self):
                return []

            def get_messages_by_session_id(self, session_id, limit):
                calls.append((session_id, limit))
                return [
                    SimpleNamespace(role="user", content="A useful question"),
                    SimpleNamespace(role="assistant", content="A useful answer"),
                ]

            def get_messages_with_summary(self, **kwargs):
                raise AssertionError("title generation must use raw history")

            def update_session_title(self, session_id, title):
                pass

        async def fake_summarize(*args, **kwargs):
            return "Useful title"

        monkeypatch.setattr(conversation_router, "get_message_store", lambda user_id: Store())
        monkeypatch.setattr(conversation_router, "_summarize_title", fake_summarize)

        result = await conversation_router.generate_title(
            conversation_router.TitleRequest(user_id="u", session_id="session-a"), None
        )

        assert result["title"] == "Useful title"
        assert calls == [("session-a", 50)]

    @pytest.mark.asyncio
    async def test_title_generation_is_idempotent_with_stored_title(self, monkeypatch):
        from src.http.routers import conversation as conversation_router

        class Store:
            def get_session_title(self, session_id):
                return "Existing title"

            def get_messages_by_session_id(self, session_id, limit):
                raise AssertionError("must not load messages when a title exists")

            def update_session_title(self, session_id, title):
                raise AssertionError("must not overwrite an existing title")

        async def fake_summarize(*args, **kwargs):
            raise AssertionError("must not call the LLM when a title exists")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda user_id: Store())
        monkeypatch.setattr(conversation_router, "_summarize_title", fake_summarize)

        result = await conversation_router.generate_title(
            conversation_router.TitleRequest(user_id="u", session_id="session-a"), None
        )

        assert result == {"title": "Existing title", "session_id": "session-a"}

    @pytest.mark.asyncio
    async def test_title_generation_passes_existing_titles_to_avoid_duplicates(self, monkeypatch):
        from types import SimpleNamespace

        from src.http.routers import conversation as conversation_router

        captured = {}

        class Store:
            def get_session_title(self, session_id):
                return None

            def get_sessions(self):
                return [
                    {"session_id": "other-1", "title": "Project planning", "created_at": ""},
                    {"session_id": "session-a", "title": "Fallback text", "created_at": ""},
                    {"session_id": "other-2", "title": "", "created_at": ""},
                ]

            def get_messages_by_session_id(self, session_id, limit):
                return [
                    SimpleNamespace(role="user", content="A useful question"),
                    SimpleNamespace(role="assistant", content="A useful answer"),
                ]

            def update_session_title(self, session_id, title):
                pass

        async def fake_summarize(*args, **kwargs):
            captured["existing_titles"] = kwargs.get("existing_titles")
            return "Useful title"

        monkeypatch.setattr(conversation_router, "get_message_store", lambda user_id: Store())
        monkeypatch.setattr(conversation_router, "_summarize_title", fake_summarize)

        result = await conversation_router.generate_title(
            conversation_router.TitleRequest(user_id="u", session_id="session-a"), None
        )

        assert result["title"] == "Useful title"
        # Only other sessions' non-empty titles are passed; the current
        # session's own fallback and empty titles are excluded.
        assert captured["existing_titles"] == ["Project planning"]

    @pytest.mark.asyncio
    async def test_summarize_title_prompt_avoids_existing_titles(self, monkeypatch):
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import Message

        captured = {}

        class FakeProvider:
            async def chat(self, **kwargs):
                captured["prompt"] = kwargs["messages"][0].content
                return Message.assistant("Roadmap review")

        def fake_create_model_from_config(model, **kwargs):
            return FakeProvider()

        monkeypatch.setattr(
            "src.sdk.providers.factory.create_model_from_config",
            fake_create_model_from_config,
        )

        title = await conversation_router._summarize_title(
            "Plan the project",
            "Here is the plan",
            user_id="title_user",
            existing_titles=["Project planning", "Meeting notes"],
        )

        assert title == "Roadmap review"
        assert "Project planning" in captured["prompt"]
        assert "Meeting notes" in captured["prompt"]
        assert "Do NOT reuse" in captured["prompt"]


class TestStreamEdgeCases:
    @pytest.mark.asyncio
    async def test_stream_message_emits_flat_canonical_sse_contract(self, monkeypatch):
        """POST /message/stream must emit flat canonical SSE events that the
        native frontend can parse: {type, data:{payload}}, not double-nested."""
        from src.http.models import MessageRequest
        from src.http.routers import conversation as conversation_router
        from src.sdk.run_events import (
            BlockDeltaData,
            ReasoningDeltaEvent,
            TextDeltaEvent,
            ToolInputStartEvent,
            ToolStartData,
        )

        store = FakeConversation()

        common = dict(
            event_id="e1", sequence=1, timestamp="2026-01-01T00:00:00Z",
            session_id="default", run_id="r", attempt=1,
        )

        async def fake_execute_stream(self, **kwargs):
            yield TextDeltaEvent(data=BlockDeltaData(block_id="b1", delta="Hello"), **common)
            yield ReasoningDeltaEvent(data=BlockDeltaData(block_id="b2", delta="Think"), **common)
            yield ToolInputStartEvent(
                data=ToolStartData(block_id="b3", tool_call_id="c1", name="email_list"),
                **common,
            )

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *a, **kw: store)
        monkeypatch.setattr(conversation_router.RunService, "execute_stream", fake_execute_stream)

        response = await conversation_router.message_stream(
            MessageRequest(message="List emails", user_id="u")
        )
        output = "".join([c async for c in response.body_iterator])

        # Flat canonical: type + data.payload at the SAME nesting level the frontend reads.
        assert '"type": "text_delta"' in output
        assert '"delta": "Hello"' in output
        assert '"type": "reasoning_delta"' in output
        assert '"delta": "Think"' in output
        assert '"type": "tool_input_start"' in output
        assert '"name": "email_list"' in output
        # The payload must NOT be double-nested under a second "data".
        import json
        for line in output.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line[len("data: "):])
                assert "type" in payload
                assert "data" in payload
                # Frontend reads payload["data"]["delta"] directly — verify it resolves.
                if payload["type"] == "text_delta":
                    assert payload["data"]["delta"] == "Hello"
                if payload["type"] == "tool_input_start":
                    assert payload["data"]["name"] == "email_list"

    @pytest.mark.skip(
        reason="handle_message no longer has a verbose/non-verbose split; run_service.execute "
        "surfaces agent failures as exceptions (handle_message returns error=str(e)), not via a "
        "StreamChunk.error -> result.error path. The session-id propagation contract is covered by "
        "test_non_verbose_message_passes_session_id_to_runner."
    )
    @pytest.mark.asyncio
    async def test_verbose_stream_error_returns_error_without_success_fallback(self, monkeypatch):
        from src.http.models import MessageRequest
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        store = FakeConversation()

        async def fake_stream(**kwargs):
            yield StreamChunk.error("boom")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
        monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

        result = await conversation_router.handle_message(
            MessageRequest(message="fail", user_id="u", verbose=True)
        )

        assert result.response == ""
        assert result.error == "boom"
        assert [args for args, _ in store.messages if args[0] == "assistant"] == []

    @pytest.mark.asyncio
    async def test_sse_interrupt_persists_partial_state_without_success_fallback(self, monkeypatch):
        from src.http.models import MessageRequest
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        store = FakeConversation()

        async def fake_stream(**kwargs):
            yield StreamChunk.text_delta("partial")
            yield StreamChunk.reasoning_delta("thinking")
            yield StreamChunk.tool_input_start("time_get", "call-1")
            yield StreamChunk.tool_result_event("time_get", "call-1", "noon")
            yield StreamChunk.interrupt("files_delete", "call-2", {"path": "x"})

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
        monkeypatch.setattr(
            conversation_router.RunService, "execute_stream",
            _make_run_event_factory(fake_stream),
        )

        response = await conversation_router.message_stream(MessageRequest(message="go", user_id="u"))
        async for _ in response.body_iterator:
            pass

        assert ("u:default") in conversation_router._pending_interrupts
        assert [(args, kwargs) for args, kwargs in store.messages if args[0] != "user"] == [
            (
                ("tool", "noon"),
                {
                    "metadata": {"tool_name": "time_get", "tool_call_id": "call-1"},
                    "session_id": "default",
                },
            ),
            (("reasoning", "thinking"), {"metadata": {}, "session_id": "default"}),
            (("assistant", "partial"), {"metadata": {"stream": True}, "session_id": "default"}),
        ]

    @pytest.mark.asyncio
    async def test_sse_pending_interrupt_stores_and_reuses_runtime_context(self, monkeypatch):
        from src.http.models import MessageRequest
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        class FakeLoop:
            def approve_tool_call(self, tool_call):
                pass

            async def _execute_tool(self, tc):
                from src.sdk.tools import ToolResult
                return ToolResult(content="noon")

        store = FakeConversation()
        captured_get_loop = {}
        captured_streams = []

        async def fake_initial_stream(**kwargs):
            yield StreamChunk.interrupt("files_delete", "call-1", {"path": "x"})

        async def fake_approve_stream(**kwargs):
            captured_streams.append(kwargs)
            yield StreamChunk.done("approved")

        async def fake_get_sdk_loop(*args, **kwargs):
            captured_get_loop.update(kwargs)
            return FakeLoop()

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
        monkeypatch.setattr(
            conversation_router.RunService, "execute_stream",
            _make_run_event_factory(fake_initial_stream),
        )

        response = await conversation_router.message_stream(
            MessageRequest(
                message="go",
                user_id="u",
                session_id="chat-1",
                model="openai:gpt-4.1",
                provider_keys={"openai": "key"},
            )
        )
        async for _ in response.body_iterator:
            pass

        assert conversation_router._pending_interrupts["u:chat-1"] == {
            "tool": "files_delete",
            "call_id": "call-1",
            "args": {"path": "x"},
            "model": "openai:gpt-4.1",
            "provider_keys": {"openai": "key"},
            "session_id": "chat-1",
        }

        monkeypatch.setattr(conversation_router, "get_sdk_loop", fake_get_sdk_loop)
        monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_approve_stream)

        approve_response = await conversation_router.approve_tool(
            conversation_router.ApproveRequest(user_id="u", call_id="call-1", session_id="chat-1")
        )
        async for _ in approve_response.body_iterator:
            pass

        assert captured_get_loop["model"] == "openai:gpt-4.1"
        assert captured_get_loop["provider_keys"] == {"openai": "key"}
        assert captured_get_loop["session_id"] == "chat-1"
        assert captured_streams[0]["model"] == "openai:gpt-4.1"
        assert captured_streams[0]["provider_keys"] == {"openai": "key"}
        assert captured_streams[0]["session_id"] == "chat-1"

    @pytest.mark.asyncio
    async def test_sse_approval_uses_pending_context_over_request_override(self, monkeypatch):
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        class FakeLoop:
            def approve_tool_call(self, tool_call):
                pass

            async def _execute_tool(self, tc):
                from src.sdk.tools import ToolResult
                return ToolResult(content="noon")

        store = FakeConversation()
        captured_get_loop = {}
        captured_streams = []
        conversation_router._pending_interrupts["u:chat-1"] = {
            "tool": "files_delete",
            "call_id": "call-1",
            "args": {"path": "x"},
            "model": "openai:gpt-4.1",
            "provider_keys": {"openai": "original"},
            "session_id": "chat-1",
        }

        async def fake_get_sdk_loop(*args, **kwargs):
            captured_get_loop.update(kwargs)
            return FakeLoop()

        async def fake_stream(**kwargs):
            captured_streams.append(kwargs)
            yield StreamChunk.done("approved")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
        monkeypatch.setattr(conversation_router, "get_sdk_loop", fake_get_sdk_loop)
        monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

        response = await conversation_router.approve_tool(
            conversation_router.ApproveRequest(
                user_id="u",
                call_id="call-1",
                session_id="chat-1",
                model="anthropic:claude-sonnet-4",
                provider_keys={"anthropic": "override"},
            )
        )
        async for _ in response.body_iterator:
            pass

        assert captured_get_loop["model"] == "openai:gpt-4.1"
        assert captured_get_loop["provider_keys"] == {"openai": "original"}
        assert captured_get_loop["session_id"] == "chat-1"
        assert captured_streams[0]["model"] == "openai:gpt-4.1"
        assert captured_streams[0]["provider_keys"] == {"openai": "original"}
        assert captured_streams[0]["session_id"] == "chat-1"

    @pytest.mark.asyncio
    async def test_sse_approval_runner_uses_scoped_summary_history(self, monkeypatch):
        from types import SimpleNamespace

        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        calls = []

        class Store:
            def get_messages_with_summary(self, *, session_id, limit):
                calls.append((session_id, limit))
                return [SimpleNamespace(role="summary", content="A summary", metadata={})]

            def get_messages_by_session_id(self, *args, **kwargs):
                raise AssertionError("approval runner used raw history")

            def add_message(self, *args, **kwargs):
                pass

        class Loop:
            def approve_tool_call(self, tool_call):
                pass

            async def _execute_tool(self, tc):
                from src.sdk.tools import ToolResult
                return ToolResult(content="noon")

        captured = {}
        conversation_router._pending_interrupts["u:session-a"] = {
            "tool": "time_get",
            "call_id": "call-1",
            "session_id": "session-a",
        }

        async def fake_get_loop(*args, **kwargs):
            return Loop()

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield StreamChunk.done("approved")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args: Store())
        monkeypatch.setattr(conversation_router, "get_sdk_loop", fake_get_loop)
        monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

        response = await conversation_router.approve_tool(
            conversation_router.ApproveRequest(
                user_id="u", call_id="call-1", session_id="session-a"
            )
        )
        async for _ in response.body_iterator:
            pass

        assert calls == [("session-a", 50)]
        assert "A summary" in str(captured["messages"][0].content)

    @pytest.mark.asyncio
    async def test_sse_error_persists_partial_state_without_success_fallback(self, monkeypatch):
        from src.http.models import MessageRequest
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        store = FakeConversation()

        async def fake_stream(**kwargs):
            yield StreamChunk.text_delta("partial")
            yield StreamChunk.reasoning_delta("thinking")
            yield StreamChunk.tool_input_start("time_get", "call-1")
            yield StreamChunk.tool_result_event("time_get", "call-1", "noon")
            yield StreamChunk.error("boom")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
        monkeypatch.setattr(
            conversation_router.RunService, "execute_stream",
            _make_run_event_factory(fake_stream),
        )

        response = await conversation_router.message_stream(MessageRequest(message="go", user_id="u"))
        async for _ in response.body_iterator:
            pass

        assert [(args, kwargs) for args, kwargs in store.messages if args[0] != "user"] == [
            (
                ("tool", "noon"),
                {
                    "metadata": {"tool_name": "time_get", "tool_call_id": "call-1"},
                    "session_id": "default",
                },
            ),
            (("reasoning", "thinking"), {"metadata": {}, "session_id": "default"}),
            (("assistant", "partial"), {"metadata": {"stream": True}, "session_id": "default"}),
        ]

    @pytest.mark.asyncio
    async def test_sse_cancel_persists_partial_state_without_success_fallback(self, monkeypatch):
        from src.http.models import MessageRequest
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        store = FakeConversation()

        async def fake_stream(**kwargs):
            yield StreamChunk.text_delta("partial")
            yield StreamChunk.reasoning_delta("thinking")
            yield StreamChunk.tool_input_start("time_get", "call-1")
            yield StreamChunk.tool_result_event("time_get", "call-1", "noon")
            conversation_router._cancel_flags["u:default"] = True
            yield StreamChunk.text_delta("ignored")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
        monkeypatch.setattr(
            conversation_router.RunService, "execute_stream",
            _make_run_event_factory(fake_stream),
        )

        response = await conversation_router.message_stream(MessageRequest(message="go", user_id="u"))
        async for _ in response.body_iterator:
            pass

        assert [(args, kwargs) for args, kwargs in store.messages if args[0] != "user"] == [
            (
                ("tool", "noon"),
                {
                    "metadata": {"tool_name": "time_get", "tool_call_id": "call-1"},
                    "session_id": "default",
                },
            ),
            (("reasoning", "thinking"), {"metadata": {}, "session_id": "default"}),
            (("assistant", "partial"), {"metadata": {"stream": True}, "session_id": "default"}),
        ]

    @pytest.mark.asyncio
    async def test_sse_cancel_disconnect_does_not_duplicate_partial_state(self, monkeypatch):
        from src.http.models import MessageRequest
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        store = FakeConversation()

        async def fake_stream(**kwargs):
            yield StreamChunk.text_delta("partial")
            conversation_router._cancel_flags["u:default"] = True
            yield StreamChunk.text_delta("ignored")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
        monkeypatch.setattr(
            conversation_router.RunService, "execute_stream",
            _make_run_event_factory(fake_stream),
        )

        response = await conversation_router.message_stream(MessageRequest(message="go", user_id="u"))
        iterator = response.body_iterator.__aiter__()
        async for chunk in iterator:
            if "cancelled" in chunk:
                await iterator.aclose()
                break

        assistant_messages = [args for args, _ in store.messages if args[0] == "assistant"]
        assert assistant_messages == [("assistant", "partial")]

    @pytest.mark.asyncio
    async def test_approve_stream_persists_tool_result_with_session_id(self, monkeypatch):
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        class FakeLoop:
            def __init__(self):
                self.approved = []

            def approve_tool_call(self, tool_call):
                self.approved.append(tool_call)

            async def _execute_tool(self, tc):
                from src.sdk.tools import ToolResult
                return ToolResult(content="noon")

        store = FakeConversation()
        conversation_router._pending_interrupts["u:default"] = {"tool": "time_get", "call_id": "call-1"}

        async def fake_get_sdk_loop(*args, **kwargs):
            return FakeLoop()

        async def fake_stream(**kwargs):
            yield StreamChunk.tool_input_start("time_get", "call-1")
            yield StreamChunk.tool_result_event("time_get", "call-1", "noon")
            yield StreamChunk.done()

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
        monkeypatch.setattr(conversation_router, "get_sdk_loop", fake_get_sdk_loop)
        monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

        response = await conversation_router.approve_tool(
            conversation_router.ApproveRequest(user_id="u", call_id="call-1")
        )
        async for _ in response.body_iterator:
            pass

        assert [(args, kwargs) for args, kwargs in store.messages if args[0] == "tool"] == [
            (
                ("tool", "noon"),
                {
                    "metadata": {"tool_name": "time_get", "tool_call_id": "call-1"},
                    "session_id": "default",
                },
            )
        ]

    @pytest.mark.asyncio
    async def test_approve_rejects_mismatched_call_id_without_approving(self, monkeypatch):
        from fastapi import HTTPException

        from src.http.routers import conversation as conversation_router

        class FakeLoop:
            def __init__(self):
                self.approved = []

            def approve_tool_call(self, tool_call):
                self.approved.append(tool_call)

            async def _execute_tool(self, tc):
                from src.sdk.tools import ToolResult
                return ToolResult(content="noon")

        loop = FakeLoop()
        conversation_router._pending_interrupts["u:default"] = {
            "tool": "time_get",
            "call_id": "call-1",
            "args": {},
        }

        async def fake_get_sdk_loop(*args, **kwargs):
            return loop

        monkeypatch.setattr(conversation_router, "get_sdk_loop", fake_get_sdk_loop)

        with pytest.raises(HTTPException) as exc:
            await conversation_router.approve_tool(
                conversation_router.ApproveRequest(user_id="u", call_id="stale")
            )

        assert exc.value.status_code == 409
        assert loop.approved == []
        assert conversation_router._pending_interrupts["u:default"]["call_id"] == "call-1"

    @pytest.mark.asyncio
    async def test_reject_rejects_mismatched_call_id(self):
        from fastapi import HTTPException

        from src.http.routers import conversation as conversation_router

        conversation_router._pending_interrupts["u:default"] = {"tool": "time_get", "call_id": "call-1"}

        with pytest.raises(HTTPException) as exc:
            await conversation_router.reject_tool(
                conversation_router.RejectRequest(user_id="u", call_id="stale")
            )

        assert exc.value.status_code == 409
        assert conversation_router._pending_interrupts["u:default"]["call_id"] == "call-1"

    @pytest.mark.asyncio
    async def test_approve_stream_error_persists_collected_tool_result(self, monkeypatch):
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        class FakeLoop:
            def __init__(self):
                self.approved = []

            def approve_tool_call(self, tool_call):
                self.approved.append(tool_call)

            async def _execute_tool(self, tc):
                from src.sdk.tools import ToolResult
                return ToolResult(content="noon")

        store = FakeConversation()
        conversation_router._pending_interrupts["u:default"] = {"tool": "time_get", "call_id": "call-1"}

        async def fake_get_sdk_loop(*args, **kwargs):
            return FakeLoop()

        async def fake_stream(**kwargs):
            yield StreamChunk.tool_input_start("time_get", "call-1")
            yield StreamChunk.tool_result_event("time_get", "call-1", "noon")
            yield StreamChunk.error("boom")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
        monkeypatch.setattr(conversation_router, "get_sdk_loop", fake_get_sdk_loop)
        monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

        response = await conversation_router.approve_tool(
            conversation_router.ApproveRequest(user_id="u", call_id="call-1")
        )
        async for _ in response.body_iterator:
            pass

        assert [(args, kwargs) for args, kwargs in store.messages if args[0] == "tool"] == [
            (
                ("tool", "noon"),
                {
                    "metadata": {"tool_name": "time_get", "tool_call_id": "call-1"},
                    "session_id": "default",
                },
            )
        ]

    @pytest.mark.asyncio
    async def test_approve_stream_cancel_persists_partial_state(self, monkeypatch):
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        class FakeLoop:
            def __init__(self):
                self.approved = []

            def approve_tool_call(self, tool_call):
                self.approved.append(tool_call)

            async def _execute_tool(self, tc):
                from src.sdk.tools import ToolResult
                return ToolResult(content="noon")

        store = FakeConversation()
        conversation_router._pending_interrupts["u:default"] = {
            "tool": "time_get",
            "call_id": "call-1",
            "args": {},
        }

        async def fake_get_sdk_loop(*args, **kwargs):
            return FakeLoop()

        async def fake_stream(**kwargs):
            yield StreamChunk.text_delta("partial")
            yield StreamChunk.reasoning_delta("thinking")
            yield StreamChunk.tool_input_start("time_get", "call-1")
            yield StreamChunk.tool_result_event("time_get", "call-1", "noon")
            conversation_router._cancel_flags["u:default"] = True
            yield StreamChunk.text_delta("ignored")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
        monkeypatch.setattr(conversation_router, "get_sdk_loop", fake_get_sdk_loop)
        monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

        response = await conversation_router.approve_tool(
            conversation_router.ApproveRequest(user_id="u", call_id="call-1")
        )
        async for _ in response.body_iterator:
            pass

        assert [(args, kwargs) for args, kwargs in store.messages if args[0] != "user"] == [
            (
                ("tool", "noon"),
                {
                    "metadata": {"tool_name": "time_get", "tool_call_id": "call-1"},
                    "session_id": "default",
                },
            ),
            (("reasoning", "thinking"), {"metadata": {}, "session_id": "default"}),
            (("assistant", "partial"), {"metadata": {"stream": True}, "session_id": "default"}),
        ]

    @pytest.mark.asyncio
    async def test_approve_stream_disconnect_persists_partial_state(self, monkeypatch):
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        class FakeLoop:
            def approve_tool_call(self, tool_call):
                pass

            async def _execute_tool(self, tc):
                from src.sdk.tools import ToolResult
                return ToolResult(content="noon")

        store = FakeConversation()
        conversation_router._pending_interrupts["u:default"] = {
            "tool": "time_get",
            "call_id": "call-1",
            "args": {},
        }

        async def fake_get_sdk_loop(*args, **kwargs):
            return FakeLoop()

        async def fake_stream(**kwargs):
            yield StreamChunk.text_delta("partial")
            yield StreamChunk.reasoning_delta("thinking")
            yield StreamChunk.tool_input_start("time_get", "call-1")
            yield StreamChunk.tool_result_event("time_get", "call-1", "noon")
            yield StreamChunk.text_delta("more")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
        monkeypatch.setattr(conversation_router, "get_sdk_loop", fake_get_sdk_loop)
        monkeypatch.setattr(conversation_router, "run_sdk_agent_stream", fake_stream)

        response = await conversation_router.approve_tool(
            conversation_router.ApproveRequest(user_id="u", call_id="call-1")
        )
        iterator = response.body_iterator.__aiter__()
        async for chunk in iterator:
            if '"content": "noon"' in chunk:
                await iterator.aclose()
                break

        assert [(args, kwargs) for args, kwargs in store.messages if args[0] != "user"] == [
            (
                ("tool", "noon"),
                {
                    "metadata": {"tool_name": "time_get", "tool_call_id": "call-1"},
                    "session_id": "default",
                },
            ),
            (("reasoning", "thinking"), {"metadata": {}, "session_id": "default"}),
            (("assistant", "partial"), {"metadata": {"stream": True}, "session_id": "default"}),
        ]

    @pytest.mark.asyncio
    async def test_stream_disconnect_after_normal_persist_does_not_duplicate_messages(self, monkeypatch):
        from src.http.models import MessageRequest
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import StreamChunk

        store = FakeConversation()

        async def fake_stream(**kwargs):
            yield StreamChunk.text_delta("```html:canvas\n<div>hi</div>\n```\nDone")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda *args, **kwargs: store)
        monkeypatch.setattr(
            conversation_router.RunService, "execute_stream",
            _make_run_event_factory(fake_stream),
        )

        response = await conversation_router.message_stream(MessageRequest(message="go", user_id="u"))
        iterator = response.body_iterator.__aiter__()
        async for chunk in iterator:
            if "canvas_update" in chunk:
                await iterator.aclose()
                break

        assistant_messages = [args for args, _ in store.messages if args[0] == "assistant"]
        # The final answer is persisted by RunService.persist_run, not the router;
        # the disconnect must not add a partial duplicate either.
        assert len(assistant_messages) == 0

    @pytest.mark.asyncio
    async def test_reject_uses_default_session_key_when_session_absent(self):
        from src.http.routers import conversation as conversation_router

        conversation_router._pending_interrupts["u:default"] = {"tool": "files_delete"}

        result = await conversation_router.reject_tool(
            conversation_router.RejectRequest(user_id="u", call_id="call-1")
        )

        assert result == {"status": "rejected", "tool": "files_delete"}

    @pytest.mark.asyncio
    async def test_non_verbose_message_passes_session_id_to_runner(self, monkeypatch):
        from src.http.models import MessageRequest
        from src.http.routers import conversation as conversation_router

        captured = {}

        class FakeConversation:
            def add_message(self, *args, **kwargs):
                return "msg-1"

            def get_messages_with_summary(self, *args, **kwargs):
                return []

        async def fake_execute(self, *, session_id, prompt, model=None, provider_keys=None, **kwargs):
            captured["session_id"] = session_id
            # Raise so handle_message returns early without needing a full RunResult.
            raise ValueError("captured")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda user_id: FakeConversation())
        monkeypatch.setattr(conversation_router.RunService, "execute", fake_execute)

        result = await conversation_router.handle_message(
            MessageRequest(message="hello", user_id="test_user", session_id="chat-1"),
            None,
        )

        assert captured["session_id"] == "chat-1"
        assert result.error == "captured"


class TestGetConversation:
    """Tests for GET /conversation."""

    def test_get_conversation_default_user(self, client):
        r = client.get("/conversation")
        assert r.status_code == 200
        data = r.json()
        assert "messages" in data
        assert isinstance(data["messages"], list)

    @pytest.mark.asyncio
    async def test_get_conversation_keeps_raw_transcript_history(self, monkeypatch):
        from src.http.routers import conversation as conversation_router

        calls = []

        class Store:
            def get_messages_by_session_id(self, session_id, limit):
                calls.append((session_id, limit))
                return []

            def get_messages_with_summary(self, **kwargs):
                raise AssertionError("GET transcript must stay raw")

        monkeypatch.setattr(conversation_router, "get_message_store", lambda user_id: Store())

        result = await conversation_router.get_conversation(
            user_id="u", session_id="session-a", limit=17
        )

        assert result == {"messages": []}
        assert calls == [("session-a", 17)]

    def test_get_conversation_with_user_id(self, client, test_user_id):
        r = client.get("/conversation", params={"user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert "messages" in data

    def test_get_conversation_with_limit(self, client, test_user_id):
        r = client.get("/conversation", params={"user_id": test_user_id, "limit": 5})
        assert r.status_code == 200

    def test_get_conversation_response_schema(self, client, test_user_id):
        r = client.get("/conversation", params={"user_id": test_user_id})
        data = r.json()
        for msg in data["messages"]:
            assert "role" in msg
            assert "content" in msg
            assert msg["role"] in ("user", "assistant", "tool", "summary")

    def test_get_conversation_ignores_workspace_id_compatibility(self, client, test_user_id):
        from src.storage.messages import get_message_store

        test_store = get_message_store(test_user_id, "test")
        test_store.clear()
        test_store.add_message(
            "user",
            "test workspace message",
            metadata={"workspace_id": "test"},
            session_id="default",
        )

        r = client.get(
            "/conversation",
            params={"user_id": test_user_id, "workspace_id": "test", "limit": 100},
        )

        assert r.status_code == 200
        assert [m["content"] for m in r.json()["messages"]] == ["test workspace message"]


class TestClearConversation:
    """Tests for DELETE /conversation."""

    def test_clear_conversation(self, client, test_user_id):
        r = client.delete("/conversation", params={"user_id": test_user_id})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "cleared"
        assert data["user_id"] == test_user_id

    def test_clear_conversation_default_user(self, client):
        r = client.delete("/conversation")
        assert r.status_code == 200


class TestEditorParser:
    """Tests for _extract_editor() and _render_editor_surface()."""

    def test_extract_editor_basic(self):
        from src.http.routers.conversation import _extract_editor

        text = '```html:editor\nfilePath: /test/file.md\n---\n\n# Hello\n\nWorld\n```'
        result = _extract_editor(text)
        assert len(result) == 1
        assert result[0]["surface_type"] == "editor"
        assert result[0]["file_path"] == "/test/file.md"
        assert "Hello" in result[0]["html"]

    def test_extract_editor_multiple(self):
        from src.http.routers.conversation import _extract_editor

        text = (
            '```html:editor\nfilePath: /a.md\n---\n\nFile A\n```\n'
            'Some text\n'
            '```html:editor\nfilePath: /b.md\n---\n\nFile B\n```'
        )
        result = _extract_editor(text)
        assert len(result) == 2

    def test_extract_editor_no_file_path(self):
        from src.http.routers.conversation import _extract_editor

        text = '```html:editor\n---\n\nContent\n```'
        result = _extract_editor(text)
        assert result == []

    def test_extract_editor_empty(self):
        from src.http.routers.conversation import _extract_editor

        text = 'No fences here'
        result = _extract_editor(text)
        assert result == []

    def test_extract_editor_interleaved_with_canvas(self):
        from src.http.routers.conversation import (
            _extract_canvas,
            _extract_editor,
            _extract_surfaces,
        )

        text = (
            '```html:canvas\n<div>hello</div>\n```\n'
            '```html:editor\nfilePath: /f.md\n---\n\ncontent\n```'
        )
        surfaces = _extract_surfaces(text)
        assert len(surfaces) == 2
        assert surfaces[0]["surface_type"] == "canvas"
        assert surfaces[1]["surface_type"] == "editor"

        canvas = _extract_canvas(text)
        assert len(canvas) == 1
        assert canvas[0]["surface_type"] == "canvas"

        editor = _extract_editor(text)
        assert len(editor) == 1
        assert editor[0]["surface_type"] == "editor"

    def test_strip_editor_fences(self):
        from src.http.routers.conversation import _strip_canvas_fences

        text = (
            'Some text\n'
            '```html:editor\nfilePath: /f.md\n---\n\ncontent\n```\n'
            'More text'
        )
        result = _strip_canvas_fences(text)
        assert "```html:editor" not in result
        assert "Some text" in result
        assert "More text" in result

    def test_render_editor_surface_contains_editor_id(self):
        from src.http.routers.conversation import _render_editor_surface

        html = _render_editor_surface("/test/file.md", "# Hello")
        assert "novel-mount" in html
        assert "# Hello" in html or "Hello" in html


class TestEditorIntegration:
    """Integration tests for editor surfaces through the HTTP endpoint."""

    def test_rest_endpoint_includes_editor_surfaces(self, client, monkeypatch):
        """REST /message verbose_data includes editor surfaces when agent emits html:editor fence."""
        editor_text = '```html:editor\nfilePath: /test/file.md\n---\n\n# Hello World\n```'

        from src.sdk.run_models import RunResult, RunStatus, RunUsage, VerificationOutcome

        async def fake_execute(self, *, session_id, prompt, model=None, provider_keys=None, **kwargs):
            return RunResult(
                run_id="r1",
                session_id=session_id,
                status=RunStatus.COMPLETED,
                attempt=1,
                model="x:y",
                response=editor_text + "Done.",
                usage=RunUsage(),
                verification=VerificationOutcome(),
            )

        import src.http.routers.conversation as conv_mod
        monkeypatch.setattr(conv_mod.RunService, "execute", fake_execute)

        r = client.post("/message", json={
            "message": "edit my file",
            "verbose": False,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["response"] == "Done."
        assert "canvas_blocks" in data.get("verbose_data", {})
        blocks = data["verbose_data"]["canvas_blocks"]
        assert len(blocks) == 1
        assert blocks[0]["surface_type"] == "editor"
        assert blocks[0]["file_path"] == "/test/file.md"

    def test_rest_endpoint_includes_both_surface_types(self, client, monkeypatch):
        """REST /message includes both canvas and editor surfaces."""
        text = (
            '```html:canvas\n<div>dashboard</div>\n```\n'
            '```html:editor\nfilePath: /f.md\n---\n\ncontent\n```\n'
            'Done.'
        )

        async def fake_execute(self, *, session_id, prompt, model=None, provider_keys=None, **kwargs):
            from src.sdk.run_models import RunResult, RunStatus, RunUsage, VerificationOutcome
            return RunResult(
                run_id="r1",
                session_id=session_id,
                status=RunStatus.COMPLETED,
                attempt=1,
                model="x:y",
                response=text,
                usage=RunUsage(),
                verification=VerificationOutcome(),
            )

        import src.http.routers.conversation as conv_mod
        monkeypatch.setattr(conv_mod.RunService, "execute", fake_execute)

        r = client.post("/message", json={
            "message": "create dashboard and editor",
            "verbose": False,
        })
        assert r.status_code == 200
        data = r.json()
        blocks = data["verbose_data"]["canvas_blocks"]
        assert len(blocks) == 2
        assert blocks[0]["surface_type"] == "canvas"
        assert blocks[1]["surface_type"] == "editor"


class TestTitleModelFromSettings:
    @pytest.mark.asyncio
    async def test_summarize_title_uses_saved_title_model(self, monkeypatch):
        from src.config.user_settings import SavedUserSettings
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import Message

        captured = {}

        class FakeProvider:
            async def chat(self, **kwargs):
                return Message.assistant("Project Planning")

        def fake_create_model_from_config(model, **kwargs):
            captured["model"] = model
            return FakeProvider()

        monkeypatch.setattr(
            "src.sdk.providers.factory.create_model_from_config",
            fake_create_model_from_config,
        )
        monkeypatch.setattr(
            "src.config.user_settings_service.load_saved_user_settings",
            lambda user_id: SavedUserSettings(title_model="anthropic:saved-title"),
        )

        title = await conversation_router._summarize_title(
            "Plan the project", "Here is the plan", user_id="title_user"
        )

        assert title == "Project Planning"
        assert captured["model"] == "anthropic:saved-title"

    @pytest.mark.asyncio
    async def test_summarize_title_falls_back_to_host_title_model(self, monkeypatch):
        from src.config.user_settings import SavedUserSettings
        from src.http.routers import conversation as conversation_router
        from src.sdk.messages import Message

        captured = {}

        class FakeProvider:
            async def chat(self, **kwargs):
                return Message.assistant("Project Planning")

        def fake_create_model_from_config(model, **kwargs):
            captured["model"] = model
            return FakeProvider()

        monkeypatch.setattr(
            "src.sdk.providers.factory.create_model_from_config",
            fake_create_model_from_config,
        )
        monkeypatch.setattr(
            "src.config.user_settings_service.load_saved_user_settings",
            lambda user_id: SavedUserSettings(),
        )

        title = await conversation_router._summarize_title(
            "Plan the project", "Here is the plan", user_id="title_user"
        )

        assert title == "Project Planning"
        assert captured["model"] == "ollama-cloud:deepseek-v4-flash:0731"
