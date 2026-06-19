"""Contract tests for WebSocket protocol messages."""


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
        data = {"type": "user_message", "content": "Hi", "user_id": "bob"}
        msg = parse_client_message(data)
        assert isinstance(msg, UserMessage)
        assert msg.content == "Hi"

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


from src.http.routers.ws import parse_directives


class TestParseDirectives:
    """Tests for directive parsing from agent response text."""

    def test_no_directives(self):
        text, events = parse_directives("Hello, how can I help?")
        assert text == "Hello, how can I help?"
        assert events == []

    def test_switch_tab(self):
        text, events = parse_directives("Here are your emails. [SWITCH_TAB:email]")
        assert "[SWITCH_TAB:email]" not in text
        assert events == [{"type": "switch_tab", "tab": "email"}]

    def test_switch_tab_lowercased(self):
        text, events = parse_directives("[SWITCH_TAB:Canvas]")
        assert events[0]["tab"] == "canvas"

    def test_switch_tab_mid_prose(self):
        text, events = parse_directives("Let me show you [SWITCH_TAB:email] your inbox.")
        assert "your inbox" in text
        assert "[SWITCH_TAB:email]" not in text
        assert len(events) == 1

    def test_multiple_switch_tab(self):
        text, events = parse_directives("[SWITCH_TAB:email][SWITCH_TAB:contacts]")
        assert len(events) == 2
        assert events[0] == {"type": "switch_tab", "tab": "email"}
        assert events[1] == {"type": "switch_tab", "tab": "contacts"}

    def test_widget_refresh_param(self):
        text, events = parse_directives("Done. [WIDGET_REFRESH:email]")
        assert "[WIDGET_REFRESH:email]" not in text
        assert events == [{"type": "widget_refresh", "tab": "email"}]

    def test_widget_refresh_all(self):
        text, events = parse_directives("Done. [WIDGET_REFRESH]")
        assert events == [{"type": "widget_refresh", "tab": "all"}]

    def test_param_refresh_before_bare(self):
        # [WIDGET_REFRESH:email] should be consumed by parameterized pattern
        # not by the bare [WIDGET_REFRESH] pattern
        text, events = parse_directives("[WIDGET_REFRESH:email][WIDGET_REFRESH]")
        assert len(events) == 2
        assert events[0] == {"type": "widget_refresh", "tab": "email"}
        assert events[1] == {"type": "widget_refresh", "tab": "all"}

    def test_new_session(self):
        text, events = parse_directives("[NEW_SESSION]")
        assert events == [{"type": "new_session"}]

    def test_all_directives_together(self):
        text, events = parse_directives(
            "[SWITCH_TAB:email][WIDGET_REFRESH:email]Check your mail[NEW_SESSION]"
        )
        assert len(events) == 3
        assert events[0] == {"type": "switch_tab", "tab": "email"}
        assert events[1] == {"type": "widget_refresh", "tab": "email"}
        assert events[2] == {"type": "new_session"}
        assert "Check your mail" in text
        assert "[" not in text

    def test_empty_string(self):
        text, events = parse_directives("")
        assert text == ""
        assert events == []

    def test_brackets_not_directives(self):
        text, events = parse_directives("Look at [this] link")
        assert text == "Look at [this] link"
        assert events == []
