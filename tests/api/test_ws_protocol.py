"""Contract tests for WebSocket protocol messages."""

import json
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from src.http.routers import ws as ws_router
from src.http.ws_protocol import (
    AiTokenMessage,
    ApproveMessage,
    CancelMessage,
    DoneMessage,
    EditAndApproveMessage,
    ErrorMessage,
    InterruptMessage,
    MiddlewareMessage,
    PingMessage,
    PongMessage,
    ReasoningMessage,
    RejectMessage,
    ToolEndMessage,
    ToolStartMessage,
    UserMessage,
    parse_client_message,
    parse_server_message,
)
from tests.api.conftest import make_run_event_factory


class TestClientMessages:
    """Tests for client → server message types."""

    def test_user_message(self):
        msg = UserMessage(content="Hello", user_id="alice")
        assert msg.type == "user_message"
        assert msg.content == "Hello"
        assert msg.user_id == "alice"
        assert msg.verbose is False

    def test_user_message_verbose(self):
        msg = UserMessage(content="Hello", verbose=True)
        assert msg.verbose is True

    def test_user_message_accepts_session_id(self):
        msg = UserMessage(content="Hello", session_id="chat-123")
        assert msg.session_id == "chat-123"

    def test_approve_message(self):
        msg = ApproveMessage(call_id="call_123")
        assert msg.type == "approve"
        assert msg.call_id == "call_123"

    def test_reject_message(self):
        msg = RejectMessage(call_id="call_123", reason="I don't want to delete that")
        assert msg.type == "reject"
        assert msg.reason == "I don't want to delete that"

    def test_reject_message_default_reason(self):
        msg = RejectMessage(call_id="call_123")
        assert msg.reason == ""

    def test_edit_and_approve_message(self):
        msg = EditAndApproveMessage(call_id="call_123", edited_args={"path": "/safe/path.txt"})
        assert msg.type == "edit_and_approve"
        assert msg.edited_args["path"] == "/safe/path.txt"

    def test_cancel_message(self):
        msg = CancelMessage()
        assert msg.type == "cancel"

    def test_ping_message(self):
        msg = PingMessage()
        assert msg.type == "ping"


class TestServerMessages:
    """Tests for server → client message types."""

    def test_ai_token_message(self):
        msg = AiTokenMessage(content="You have", session_id="sess_123")
        assert msg.type == "ai_token"
        assert msg.content == "You have"
        assert msg.session_id == "sess_123"

    def test_tool_start_message(self):
        msg = ToolStartMessage(tool="email_list", call_id="call_abc", args={"folder": "INBOX"})
        assert msg.type == "tool_start"
        assert msg.tool == "email_list"
        assert msg.args == {"folder": "INBOX"}

    def test_tool_start_message_default_args(self):
        msg = ToolStartMessage(tool="time_get", call_id="call_123")
        assert msg.args == {}

    def test_tool_end_message(self):
        msg = ToolEndMessage(tool="email_list", call_id="call_abc", result_preview="Found 5 emails")
        assert msg.type == "tool_end"
        assert msg.result_preview == "Found 5 emails"

    def test_interrupt_message(self):
        msg = InterruptMessage(
            call_id="call_xyz",
            tool="files_delete",
            args={"path": "/important.txt"},
        )
        assert msg.type == "interrupt"
        assert msg.tool == "files_delete"
        assert msg.allowed_actions == ["approve", "reject", "edit"]

    def test_middleware_message(self):
        msg = MiddlewareMessage(name="MemoryMiddleware", event="before_agent", data={"memories": 5})
        assert msg.type == "middleware"
        assert msg.name == "MemoryMiddleware"

    def test_reasoning_message(self):
        msg = ReasoningMessage(content="Let me think...", session_id="sess_1")
        assert msg.type == "reasoning"

    def test_done_message(self):
        msg = DoneMessage(response="You have 3 meetings", tool_calls=[{"name": "calendar"}])
        assert msg.type == "done"
        assert msg.response == "You have 3 meetings"
        assert len(msg.tool_calls) == 1

    def test_error_message(self):
        msg = ErrorMessage(message="Connection failed", code="MODEL_ERROR")
        assert msg.type == "error"
        assert msg.code == "MODEL_ERROR"

    def test_pong_message(self):
        msg = PongMessage()
        assert msg.type == "pong"


class TestParseClientMessage:
    """Tests for parsing client messages from raw dicts."""

    def test_parse_user_message(self):
        data = {"type": "user_message", "content": "Hi", "user_id": "bob", "session_id": "chat-1"}
        msg = parse_client_message(data)
        assert isinstance(msg, UserMessage)
        assert msg.content == "Hi"
        assert msg.session_id == "chat-1"

    def test_parse_approve_message(self):
        data = {"type": "approve", "call_id": "call_1"}
        msg = parse_client_message(data)
        assert isinstance(msg, ApproveMessage)
        assert msg.call_id == "call_1"

    def test_parse_unknown_type(self):
        data = {"type": "future_message", "data": "something"}
        msg = parse_client_message(data)
        assert msg is None

    def test_parse_invalid_data(self):
        data = {"type": "user_message"}
        msg = parse_client_message(data)
        assert msg is None

    def test_parse_cancel(self):
        data = {"type": "cancel"}
        msg = parse_client_message(data)
        assert isinstance(msg, CancelMessage)

    def test_parse_ping(self):
        data = {"type": "ping"}
        msg = parse_client_message(data)
        assert isinstance(msg, PingMessage)


class TestParseServerMessage:
    """Tests for parsing server messages from raw dicts."""

    def test_parse_ai_token(self):
        data = {"type": "ai_token", "content": "Hello"}
        msg = parse_server_message(data)
        assert isinstance(msg, AiTokenMessage)
        assert msg.content == "Hello"

    def test_parse_tool_start(self):
        data = {"type": "tool_start", "tool": "time_get", "call_id": "c1"}
        msg = parse_server_message(data)
        assert isinstance(msg, ToolStartMessage)

    def test_parse_done(self):
        data = {"type": "done", "response": "Done!", "tool_calls": []}
        msg = parse_server_message(data)
        assert isinstance(msg, DoneMessage)
        assert msg.response == "Done!"

    def test_parse_error(self):
        data = {"type": "error", "message": "failed", "code": "ERR"}
        msg = parse_server_message(data)
        assert isinstance(msg, ErrorMessage)

    def test_parse_unknown_type(self):
        data = {"type": "future_event", "data": "something"}
        msg = parse_server_message(data)
        assert msg is None


class TestMessageSerialization:
    """Tests for JSON round-trip of messages."""

    def test_user_message_roundtrip(self):
        msg = UserMessage(content="What's the weather?", user_id="alice", verbose=True)
        json_data = msg.model_dump()
        restored = UserMessage(**json_data)
        assert restored.content == msg.content
        assert restored.user_id == msg.user_id
        assert restored.verbose == msg.verbose

    def test_interrupt_message_roundtrip(self):
        msg = InterruptMessage(
            call_id="call_1",
            tool="files_delete",
            args={"path": "/test.txt"},
            allowed_actions=["approve", "reject"],
        )
        json_data = msg.model_dump()
        restored = InterruptMessage(**json_data)
        assert restored.call_id == msg.call_id
        assert restored.tool == msg.tool
        assert restored.args == msg.args
        assert restored.allowed_actions == msg.allowed_actions

    def test_done_message_roundtrip(self):
        msg = DoneMessage(response="Complete", tool_calls=[{"name": "time_get", "id": "c1"}])
        json_data = msg.model_dump()
        restored = DoneMessage(**json_data)
        assert restored.response == "Complete"
        assert len(restored.tool_calls) == 1


class TestWebSocketPersistence:
    @pytest.mark.asyncio
    async def test_ws_runner_uses_only_session_scoped_summary_history(self, monkeypatch):
        calls = []
        captured = {}

        class Store:
            def add_message(self, *args, **kwargs):
                return "msg-1"

            def get_messages_with_summary(self, *, session_id, limit):
                calls.append((session_id, limit))
                sessions = {
                    "session-a": [
                        SimpleNamespace(role="summary", content="A summary", metadata={}),
                        SimpleNamespace(role="user", content="A retained", metadata={}),
                    ],
                    "session-b": [
                        SimpleNamespace(role="summary", content="B sentinel", metadata={})
                    ],
                }
                return sessions[session_id]

            def get_messages_by_session_id(self, *args, **kwargs):
                raise AssertionError("WebSocket runner used raw history")

        class FakeWebSocket:
            client = None

            def __init__(self):
                self.messages = [
                    json.dumps(
                        {
                            "type": "user_message",
                            "content": "new A",
                            "user_id": "u",
                            "session_id": "session-a",
                        }
                    )
                ]

            async def accept(self):
                pass

            async def receive_text(self):
                if self.messages:
                    return self.messages.pop(0)
                raise WebSocketDisconnect()

            async def send_json(self, payload):
                pass

        async def fake_run_agent_stream(*args, **kwargs):
            captured["messages"] = args[2]

        monkeypatch.setattr(
            ws_router,
            "get_settings",
            lambda: SimpleNamespace(
                auth=SimpleNamespace(api_key="", solo_bypass=True),
                verification=SimpleNamespace(enabled=False),
            ),
        )
        monkeypatch.setattr(ws_router, "get_message_store", lambda *args: Store())
        monkeypatch.setattr(ws_router, "_run_agent_stream", fake_run_agent_stream)

        await ws_router.ws_conversation(FakeWebSocket())

        contents = [str(message.content) for message in captured["messages"]]
        assert calls == [("session-a", 50)]
        assert any("A summary" in content for content in contents)
        assert all("B sentinel" not in content for content in contents)

    @pytest.mark.asyncio
    async def test_run_agent_stream_accepts_legacy_only_alias_chunks(self, monkeypatch):
        from src.sdk.messages import StreamChunk

        class FakeConversation:
            def __init__(self):
                self.calls = []

            def add_message(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return "msg-1"

        class FakeWebSocket:
            def __init__(self):
                self.sent = []

            async def send_json(self, payload):
                self.sent.append(payload)

        async def fake_run_sdk_agent_stream(**kwargs):
            yield StreamChunk.ai_token("Hello")
            yield StreamChunk.reasoning("Think")
            yield StreamChunk.tool_start("email_list", "call-1")
            yield StreamChunk.tool_result_event("email_list", "call-1", "result")
            yield StreamChunk.done()

        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))
        conversation = FakeConversation()
        websocket = FakeWebSocket()

        await ws_router._run_agent_stream(websocket, "test_user", [], conversation, session_id="chat-1")

        # The WS endpoint now forwards canonical RunEvent envelopes (dc5eed0 removed
        # the legacy ai_token/reasoning/tool_start wire names).
        assert any(m.get("type") == "text_delta" and m.get("data", {}).get("delta") == "Hello" for m in websocket.sent)
        assert any(m.get("type") == "reasoning_delta" and m.get("data", {}).get("delta") == "Think" for m in websocket.sent)
        assert any(m.get("type") == "tool_input_start" and m.get("data", {}).get("name") == "email_list" for m in websocket.sent)
        # Success-path persistence is owned by RunService.persist_run (tools
        # as audit records, reasoning as pre-messages, the answer as the run's
        # final message) — the WS router persists nothing on success.
        assert conversation.calls == []

    @pytest.mark.asyncio
    async def test_run_agent_stream_cancelled_error_persists_partial_state(self, monkeypatch):
        import asyncio

        from src.sdk.messages import StreamChunk

        class FakeConversation:
            def __init__(self):
                self.calls = []

            def add_message(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return "msg-1"

        class FakeWebSocket:
            async def send_json(self, payload):
                pass

        async def fake_run_sdk_agent_stream(**kwargs):
            yield StreamChunk.text_delta("partial")
            yield StreamChunk.reasoning_delta("thinking")
            raise asyncio.CancelledError

        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))
        conversation = FakeConversation()

        with pytest.raises(asyncio.CancelledError):
            await ws_router._run_agent_stream(FakeWebSocket(), "test_user", [], conversation, session_id="chat-1")

        assert conversation.calls == [
            (("reasoning", "thinking"), {"metadata": {}, "session_id": "chat-1"}),
            (("assistant", "partial"), {"metadata": {"stream": True}, "session_id": "chat-1"}),
        ]

    def test_user_message_uses_session_id_not_workspace_metadata(self):
        calls = []

        class FakeConversation:
            def add_message(self, *args, **kwargs):
                calls.append((args, kwargs))
                return "msg-1"

        ws_router._persist_ws_conversation_message(
            FakeConversation(), "user", "hello", session_id="chat-1"
        )

        assert calls == [(('user', 'hello'), {'metadata': {}, 'session_id': 'chat-1'})]

    def test_resolve_session_uses_client_session_id(self):
        msg = UserMessage(content="hello", session_id="client-chat")

        assert ws_router._resolve_ws_session_id(msg, "generated") == "client-chat"

    def test_resolve_session_uses_generated_fallback_when_absent(self):
        msg = UserMessage(content="hello")

        assert ws_router._resolve_ws_session_id(msg, "generated") == "generated"

    @pytest.mark.asyncio
    async def test_run_agent_stream_passes_session_id_to_sdk_stream(self, monkeypatch):
        captured = {}

        async def fake_run_sdk_agent_stream(**kwargs):
            captured.update(kwargs)
            if False:
                yield None

        class FakeWebSocket:
            async def send_json(self, payload):
                pass

        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))

        await ws_router._run_agent_stream(
            FakeWebSocket(),
            "test_user",
            [],
            object(),
            session_id="chat-1",
            workspace_id="project",
        )

        assert captured["session_id"] == "chat-1"

    @pytest.mark.skip(
        reason="cancel_event is no longer passed down to the stream function: the WS router "
        "checks it in its own loop (RunService owns cancellation via SessionLock). The "
        "router-level cancel contract is covered by "
        "test_run_agent_stream_persists_partial_state_on_cancel_event."
    )
    @pytest.mark.asyncio
    async def test_run_agent_stream_passes_cancel_event_to_sdk_stream(self, monkeypatch):
        import asyncio

        captured = {}

        async def fake_run_sdk_agent_stream(**kwargs):
            captured.update(kwargs)
            if False:
                yield None

        class FakeWebSocket:
            async def send_json(self, payload):
                pass

        cancel_event = asyncio.Event()
        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))

        await ws_router._run_agent_stream(
            FakeWebSocket(),
            "test_user",
            [],
            object(),
            session_id="chat-1",
            cancel_event=cancel_event,
        )

        assert captured["cancel_event"] is cancel_event

    @pytest.mark.asyncio
    async def test_run_agent_stream_ignores_tool_end_when_tool_result_arrives(self, monkeypatch):
        from src.sdk.messages import StreamChunk

        class FakeConversation:
            def __init__(self):
                self.calls = []

            def add_message(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return "msg-1"

        class FakeWebSocket:
            def __init__(self):
                self.sent = []

            async def send_json(self, payload):
                self.sent.append(payload)

        async def fake_run_sdk_agent_stream(**kwargs):
            yield StreamChunk.tool_input_start("email_list", "call-1")
            yield StreamChunk.tool_end("email_list", "call-1", "legacy")
            yield StreamChunk.tool_result_event("email_list", "call-1", "canonical")
            yield StreamChunk.done()

        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))

        conversation = FakeConversation()
        websocket = FakeWebSocket()
        await ws_router._run_agent_stream(
            websocket,
            "test_user",
            [],
            conversation,
            session_id="chat-1",
        )

        tool_result_payloads = [m for m in websocket.sent if m.get("type") == "tool_result"]
        assert [m["result_preview"] for m in tool_result_payloads] == ["canonical"]
        # The router forwards the canonical result but does not persist it on
        # success — RunService.persist_run owns the tool audit records.
        assert [args for args, kwargs in conversation.calls if args[0] == "tool"] == []

    @pytest.mark.asyncio
    async def test_run_agent_stream_persists_partial_state_on_interrupt(self, monkeypatch):
        from src.sdk.messages import StreamChunk

        class FakeConversation:
            def __init__(self):
                self.calls = []

            def add_message(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return "msg-1"

        class FakeWebSocket:
            async def send_json(self, payload):
                pass

        async def fake_run_sdk_agent_stream(**kwargs):
            yield StreamChunk.text_delta("partial")
            yield StreamChunk.reasoning_delta("thinking")
            yield StreamChunk.tool_input_start("time_get", "call-1")
            yield StreamChunk.tool_result_event("time_get", "call-1", "noon")
            yield StreamChunk.interrupt("files_delete", "call-2", {"path": "x"})

        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))

        conversation = FakeConversation()
        pending = [None]
        await ws_router._run_agent_stream(
            FakeWebSocket(), "test_user", [], conversation, session_id="chat-1", pending_ref=pending
        )

        assert pending[0] == {
            "tool": "files_delete",
            "call_id": "call-2",
            "args": {"path": "x"},
            "model": None,
            "provider_keys": None,
            "session_id": "chat-1",
        }
        assert [(args, kwargs) for args, kwargs in conversation.calls] == [
            (
                ("tool", "noon"),
                {
                    "metadata": {"tool_name": "time_get", "tool_call_id": "call-1"},
                    "session_id": "chat-1",
                },
            ),
            (("reasoning", "thinking"), {"metadata": {}, "session_id": "chat-1"}),
            (("assistant", "partial"), {"metadata": {"stream": True}, "session_id": "chat-1"}),
        ]

    @pytest.mark.asyncio
    async def test_run_agent_stream_done_uses_done_content_when_no_text_delta(self, monkeypatch):
        from src.sdk.messages import StreamChunk

        class FakeConversation:
            def __init__(self):
                self.calls = []

            def add_message(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return "msg-1"

        class FakeWebSocket:
            def __init__(self):
                self.sent = []

            async def send_json(self, payload):
                self.sent.append(payload)

        async def fake_run_sdk_agent_stream(**kwargs):
            yield StreamChunk.done("final answer")

        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))

        conversation = FakeConversation()
        websocket = FakeWebSocket()
        await ws_router._run_agent_stream(websocket, "test_user", [], conversation, session_id="chat-1")

        # The final answer is persisted by RunService.persist_run, not the router;
        # the done message carries the response and the persisted message id.
        assert [m for m in conversation.calls if m[0][0] == "assistant"] == []
        done = [m for m in websocket.sent if m.get("type") == "done"][0]
        assert done["response"] == "final answer"
        assert done["message_id"] == "msg-1"

    @pytest.mark.asyncio
    async def test_run_agent_stream_skips_empty_tool_results(self, monkeypatch):
        from src.sdk.messages import StreamChunk

        class FakeConversation:
            def __init__(self):
                self.calls = []

            def add_message(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return "msg-1"

        class FakeWebSocket:
            async def send_json(self, payload):
                pass

        async def fake_run_sdk_agent_stream(**kwargs):
            yield StreamChunk.tool_input_start("time_get", "call-1")
            yield StreamChunk.tool_result_event("time_get", "call-1", "")
            yield StreamChunk.done()

        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))

        conversation = FakeConversation()
        await ws_router._run_agent_stream(FakeWebSocket(), "test_user", [], conversation, session_id="chat-1")

        assert [args for args, _ in conversation.calls if args[0] == "tool"] == []

    @pytest.mark.asyncio
    async def test_run_agent_stream_persists_partial_state_on_stream_error(self, monkeypatch):
        from src.sdk.messages import StreamChunk

        class FakeConversation:
            def __init__(self):
                self.calls = []

            def add_message(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return "msg-1"

        class FakeWebSocket:
            async def send_json(self, payload):
                pass

        async def fake_run_sdk_agent_stream(**kwargs):
            yield StreamChunk.text_delta("partial")
            yield StreamChunk.reasoning_delta("thinking")
            raise RuntimeError("stream lost")

        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))

        conversation = FakeConversation()
        await ws_router._run_agent_stream(FakeWebSocket(), "test_user", [], conversation, session_id="chat-1")

        assert [(args, kwargs) for args, kwargs in conversation.calls] == [
            (("reasoning", "thinking"), {"metadata": {}, "session_id": "chat-1"}),
            (("assistant", "partial"), {"metadata": {"stream": True}, "session_id": "chat-1"}),
        ]

    @pytest.mark.asyncio
    async def test_ws_approval_get_sdk_loop_receives_session_id(self, monkeypatch):
        captured_calls = []
        history_calls = []
        stream_calls = 0

        class FakeLoop:
            def __init__(self):
                self.approved = []

            def approve_tool_call(self, tool_call):
                self.approved.append(tool_call)

        class FakeConversation:
            def add_message(self, *args, **kwargs):
                return "msg-1"

            def get_messages_with_summary(self, *, session_id, limit):
                history_calls.append((session_id, limit))
                return []

            def get_messages_by_session_id(self, *args, **kwargs):
                raise AssertionError("approval runner used raw history")

        class FakeWebSocket:
            client = None

            def __init__(self):
                self.messages = [
                    json.dumps(
                        {
                            "type": "user_message",
                            "content": "delete it",
                            "user_id": "test_user",
                            "workspace_id": "project",
                            "session_id": "chat-1",
                        }
                    ),
                    json.dumps({"type": "approve", "call_id": "call-1"}),
                ]
                self.sent = []

            async def accept(self):
                pass

            async def receive_text(self):
                if not self.messages:
                    raise WebSocketDisconnect()
                return self.messages.pop(0)

            async def send_json(self, payload):
                self.sent.append(payload)

        async def fake_run_agent_stream(*args, **kwargs):
            nonlocal stream_calls
            stream_calls += 1
            pending_ref = kwargs["pending_ref"]
            if stream_calls == 1:
                pending_ref[0] = {"tool": "files_delete", "call_id": "call-1"}

        async def fake_get_sdk_loop(*args, **kwargs):
            captured_calls.append((args, kwargs))
            return FakeLoop()

        monkeypatch.setattr(
            ws_router,
            "get_settings",
            lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
        )
        monkeypatch.setattr(ws_router, "get_message_store", lambda *args, **kwargs: FakeConversation())
        monkeypatch.setattr(ws_router, "_run_agent_stream", fake_run_agent_stream)
        monkeypatch.setattr(ws_router, "get_sdk_loop", fake_get_sdk_loop)

        await ws_router.ws_conversation(FakeWebSocket())

        assert captured_calls
        assert captured_calls[0][1]["session_id"] == "chat-1"
        assert history_calls == [("chat-1", 50), ("chat-1", 50)]

    @pytest.mark.asyncio
    async def test_ws_cancel_sets_running_stream_cancel_event(self, monkeypatch):
        captured = {}

        class FakeConversation:
            def add_message(self, *args, **kwargs):
                return "msg-1"

            def get_messages_with_summary(self, *, session_id, limit):
                return []

        class FakeWebSocket:
            client = None

            def __init__(self):
                self.messages = [
                    json.dumps({"type": "user_message", "content": "run", "user_id": "test_user"}),
                    json.dumps({"type": "cancel"}),
                ]
                self.sent = []

            async def accept(self):
                pass

            async def receive_text(self):
                if not self.messages:
                    raise WebSocketDisconnect()
                return self.messages.pop(0)

            async def send_json(self, payload):
                self.sent.append(payload)

        async def fake_run_agent_stream(*args, **kwargs):
            cancel_event = kwargs["cancel_event"]
            captured["cancel_event"] = cancel_event
            await cancel_event.wait()

        monkeypatch.setattr(
            ws_router,
            "get_settings",
            lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
        )
        monkeypatch.setattr(ws_router, "get_message_store", lambda *args, **kwargs: FakeConversation())
        monkeypatch.setattr(ws_router, "_run_agent_stream", fake_run_agent_stream)

        websocket = FakeWebSocket()
        await ws_router.ws_conversation(websocket)

        assert captured["cancel_event"].is_set()
        assert any(m.get("type") == "done" and m.get("response") == "Cancelled" for m in websocket.sent)

    @pytest.mark.asyncio
    async def test_ws_cancel_actively_cancels_running_stream_task(self, monkeypatch):
        import asyncio

        captured = {}
        stream_started = asyncio.Event()

        class FakeConversation:
            def add_message(self, *args, **kwargs):
                return "msg-1"

            def get_messages_with_summary(self, *, session_id, limit):
                return []

        class FakeWebSocket:
            client = None

            def __init__(self):
                self.messages = [
                    json.dumps({"type": "user_message", "content": "run", "user_id": "test_user"}),
                    json.dumps({"type": "cancel"}),
                ]
                self.sent = []

            async def accept(self):
                pass

            async def receive_text(self):
                if not self.messages:
                    raise WebSocketDisconnect()
                if len(self.messages) == 1:
                    await stream_started.wait()
                return self.messages.pop(0)

            async def send_json(self, payload):
                self.sent.append(payload)

        async def fake_run_agent_stream(*args, **kwargs):
            captured["cancel_event"] = kwargs["cancel_event"]
            stream_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                captured["task_cancelled"] = True
                raise

        monkeypatch.setattr(
            ws_router,
            "get_settings",
            lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
        )
        monkeypatch.setattr(ws_router, "get_message_store", lambda *args, **kwargs: FakeConversation())
        monkeypatch.setattr(ws_router, "_run_agent_stream", fake_run_agent_stream)

        websocket = FakeWebSocket()
        await ws_router.ws_conversation(websocket)

        assert captured["cancel_event"].is_set()
        assert captured["task_cancelled"] is True
        done_messages = [m for m in websocket.sent if m.get("type") == "done"]
        assert len(done_messages) == 1
        assert done_messages[0]["response"] == "Cancelled"

    @pytest.mark.asyncio
    async def test_run_agent_stream_suppresses_done_after_cancel_event(self, monkeypatch):
        import asyncio

        from src.sdk.messages import StreamChunk

        class FakeConversation:
            def __init__(self):
                self.calls = []

            def add_message(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return "msg-1"

        class FakeWebSocket:
            def __init__(self):
                self.sent = []

            async def send_json(self, payload):
                self.sent.append(payload)

        async def fake_run_sdk_agent_stream(**kwargs):
            yield StreamChunk.text_delta("partial")
            kwargs["cancel_event"].set()
            yield StreamChunk.done("normal done")

        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))
        websocket = FakeWebSocket()
        conversation = FakeConversation()
        await ws_router._run_agent_stream(
            websocket,
            "test_user",
            [],
            conversation,
            session_id="chat-1",
            cancel_event=asyncio.Event(),
        )

        assert [m for m in websocket.sent if m.get("type") == "done"] == []
        assert [args for args, _ in conversation.calls if args[0] == "assistant"] == [
            ("assistant", "partial")
        ]

    @pytest.mark.asyncio
    async def test_ws_interrupt_remembers_model_provider_keys_and_session_for_approval(self, monkeypatch):
        from src.sdk.messages import StreamChunk

        class FakeConversation:
            def __init__(self):
                self.calls = []

            def add_message(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return "msg-1"

        class FakeWebSocket:
            def __init__(self):
                self.sent = []

            async def send_json(self, payload):
                self.sent.append(payload)

        pending = [None]

        async def fake_run_sdk_agent_stream(**kwargs):
            yield StreamChunk.interrupt("files_delete", "call-1", {"path": "x"})

        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))

        await ws_router._run_agent_stream(
            FakeWebSocket(),
            "u",
            [],
            FakeConversation(),
            session_id="chat-1",
            pending_ref=pending,
            model="openai:gpt-4.1",
            provider_keys={"openai": "key"},
        )

        assert pending[0] == {
            "tool": "files_delete",
            "call_id": "call-1",
            "args": {"path": "x"},
            "model": "openai:gpt-4.1",
            "provider_keys": {"openai": "key"},
            "session_id": "chat-1",
        }

    def test_ws_pending_runtime_context_prefers_pending_over_override(self):
        pending = {
            "model": "openai:gpt-4.1",
            "provider_keys": {"openai": "original"},
            "session_id": "chat-1",
        }

        result = ws_router._pending_runtime_context(
            pending,
            session_id="chat-2",
            model="anthropic:claude-sonnet-4",
            provider_keys={"anthropic": "override"},
        )

        assert result == ("chat-1", "openai:gpt-4.1", {"openai": "original"})

    @pytest.mark.asyncio
    async def test_ws_pending_loop_handles_edit_and_approve(self, monkeypatch):
        stream_calls = 0
        history_calls = []

        class FakeLoop:
            def __init__(self):
                self.approved = []

            def approve_tool_call(self, tool_call):
                self.approved.append(tool_call)

        class FakeConversation:
            def add_message(self, *args, **kwargs):
                return "msg-1"

            def get_messages_with_summary(self, *, session_id, limit):
                history_calls.append((session_id, limit))
                return []

            def get_messages_by_session_id(self, *args, **kwargs):
                raise AssertionError("edit-and-approve runner used raw history")

        class FakeWebSocket:
            client = None

            def __init__(self):
                self.messages = [
                    json.dumps(
                        {
                            "type": "user_message",
                            "content": "delete",
                            "user_id": "test_user",
                            "session_id": "session-a",
                        }
                    ),
                    json.dumps(
                        {
                            "type": "edit_and_approve",
                            "call_id": "call-1",
                            "edited_args": {"path": "/edited"},
                        }
                    ),
                ]
                self.sent = []

            async def accept(self):
                pass

            async def receive_text(self):
                if not self.messages:
                    raise WebSocketDisconnect()
                return self.messages.pop(0)

            async def send_json(self, payload):
                self.sent.append(payload)

        async def fake_run_agent_stream(*args, **kwargs):
            nonlocal stream_calls
            stream_calls += 1
            if stream_calls == 1:
                kwargs["pending_ref"][0] = {
                    "tool": "files_delete",
                    "call_id": "call-1",
                    "args": {"path": "/old"},
                }

        loop = FakeLoop()

        async def fake_get_sdk_loop(*args, **kwargs):
            return loop

        monkeypatch.setattr(
            ws_router,
            "get_settings",
            lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
        )
        monkeypatch.setattr(ws_router, "get_message_store", lambda *args, **kwargs: FakeConversation())
        monkeypatch.setattr(ws_router, "_run_agent_stream", fake_run_agent_stream)
        monkeypatch.setattr(ws_router, "get_sdk_loop", fake_get_sdk_loop)

        await ws_router.ws_conversation(FakeWebSocket())

        assert stream_calls == 2
        assert loop.approved[0].arguments == {"path": "/edited"}
        assert history_calls == [("session-a", 50), ("session-a", 50)]

    @pytest.mark.asyncio
    async def test_ws_approval_rejects_mismatched_call_id(self, monkeypatch):
        class FakeLoop:
            def __init__(self):
                self.approved = []

            def approve_tool_call(self, tool_call):
                self.approved.append(tool_call)

        class FakeConversation:
            def add_message(self, *args, **kwargs):
                return "msg-1"

            def get_messages_with_summary(self, *, session_id, limit):
                return []

        class FakeWebSocket:
            client = None

            def __init__(self):
                self.messages = [
                    json.dumps({"type": "user_message", "content": "delete", "user_id": "test_user"}),
                    json.dumps({"type": "approve", "call_id": "stale"}),
                ]
                self.sent = []

            async def accept(self):
                pass

            async def receive_text(self):
                if not self.messages:
                    raise WebSocketDisconnect()
                return self.messages.pop(0)

            async def send_json(self, payload):
                self.sent.append(payload)

        async def fake_run_agent_stream(*args, **kwargs):
            kwargs["pending_ref"][0] = {"tool": "files_delete", "call_id": "call-1", "args": {}}

        loop = FakeLoop()

        async def fake_get_sdk_loop(*args, **kwargs):
            return loop

        monkeypatch.setattr(
            ws_router,
            "get_settings",
            lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
        )
        monkeypatch.setattr(ws_router, "get_message_store", lambda *args, **kwargs: FakeConversation())
        monkeypatch.setattr(ws_router, "_run_agent_stream", fake_run_agent_stream)
        monkeypatch.setattr(ws_router, "get_sdk_loop", fake_get_sdk_loop)

        await ws_router.ws_conversation(FakeWebSocket())

        assert loop.approved == []

    @pytest.mark.asyncio
    async def test_ws_approve_without_pending_sends_no_pending_error(self, monkeypatch):
        class FakeWebSocket:
            client = None

            def __init__(self):
                self.messages = [json.dumps({"type": "approve", "call_id": "call-1"})]
                self.sent = []

            async def accept(self):
                pass

            async def receive_text(self):
                if not self.messages:
                    raise WebSocketDisconnect()
                return self.messages.pop(0)

            async def send_json(self, payload):
                self.sent.append(payload)

        monkeypatch.setattr(
            ws_router,
            "get_settings",
            lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
        )

        websocket = FakeWebSocket()
        await ws_router.ws_conversation(websocket)

        assert websocket.sent == [
            {
                "type": "error",
                "message": "No pending tool call to approve",
                "code": "NO_PENDING_INTERRUPT",
            }
        ]

    @pytest.mark.asyncio
    async def test_run_agent_stream_persists_partial_state_on_cancel_event(self, monkeypatch):
        import asyncio

        from src.sdk.messages import StreamChunk

        class FakeConversation:
            def __init__(self):
                self.calls = []

            def add_message(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return "msg-1"

        class FakeWebSocket:
            async def send_json(self, payload):
                pass

        async def fake_run_sdk_agent_stream(**kwargs):
            yield StreamChunk.text_delta("partial")
            yield StreamChunk.reasoning_delta("thinking")
            yield StreamChunk.tool_input_start("time_get", "call-1")
            yield StreamChunk.tool_result_event("time_get", "call-1", "noon")
            kwargs["cancel_event"].set()
            yield StreamChunk.done("normal done")

        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))
        conversation = FakeConversation()
        await ws_router._run_agent_stream(
            FakeWebSocket(),
            "test_user",
            [],
            conversation,
            session_id="chat-1",
            cancel_event=asyncio.Event(),
        )

        assert conversation.calls == [
            (
                ("tool", "noon"),
                {
                    "metadata": {"tool_name": "time_get", "tool_call_id": "call-1"},
                    "session_id": "chat-1",
                },
            ),
            (("reasoning", "thinking"), {"metadata": {}, "session_id": "chat-1"}),
            (("assistant", "partial"), {"metadata": {"stream": True}, "session_id": "chat-1"}),
        ]

    @pytest.mark.asyncio
    async def test_run_agent_stream_send_done_failure_does_not_duplicate_persistence(self, monkeypatch):
        from src.sdk.messages import StreamChunk

        class FakeConversation:
            def __init__(self):
                self.calls = []

            def add_message(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return "msg-1"

        class FakeWebSocket:
            async def send_json(self, payload):
                if payload.get("type") == "done":
                    raise RuntimeError("send failed")

        async def fake_run_sdk_agent_stream(**kwargs):
            yield StreamChunk.text_delta("final")
            yield StreamChunk.reasoning_delta("thinking")
            yield StreamChunk.tool_input_start("time_get", "call-1")
            yield StreamChunk.tool_result_event("time_get", "call-1", "noon")
            yield StreamChunk.done()

        monkeypatch.setattr(ws_router.RunService, "execute_stream", make_run_event_factory(fake_run_sdk_agent_stream))
        conversation = FakeConversation()

        await ws_router._run_agent_stream(
            FakeWebSocket(), "test_user", [], conversation, session_id="chat-1"
        )

        # A done-send failure must not trigger the failure-path fallback
        # persist (the success path persisted nothing — RunService owns it),
        # so nothing is written twice.
        assert conversation.calls == []

    @pytest.mark.asyncio
    async def test_ws_disconnect_cancels_running_stream_task(self, monkeypatch):
        captured = {}

        class FakeConversation:
            def add_message(self, *args, **kwargs):
                return "msg-1"

            def get_messages_with_summary(self, *, session_id, limit):
                return []

        class FakeWebSocket:
            client = None

            def __init__(self):
                self.messages = [
                    json.dumps({"type": "user_message", "content": "run", "user_id": "test_user"}),
                ]

            async def accept(self):
                pass

            async def receive_text(self):
                if self.messages:
                    return self.messages.pop(0)
                raise WebSocketDisconnect()

            async def send_json(self, payload):
                pass

        async def fake_run_agent_stream(*args, **kwargs):
            cancel_event = kwargs["cancel_event"]
            captured["cancel_event"] = cancel_event
            try:
                await cancel_event.wait()
            finally:
                captured["cleaned_up"] = True

        monkeypatch.setattr(
            ws_router,
            "get_settings",
            lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
        )
        monkeypatch.setattr(ws_router, "get_message_store", lambda *args, **kwargs: FakeConversation())
        monkeypatch.setattr(ws_router, "_run_agent_stream", fake_run_agent_stream)

        await ws_router.ws_conversation(FakeWebSocket())

        assert captured["cancel_event"].is_set()
        assert captured["cleaned_up"] is True
