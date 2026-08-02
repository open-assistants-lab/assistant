"""Tests for SDK message types — Message, ToolCall, StreamChunk."""

import json

from src.sdk.messages import Message, StreamChunk, ToolCall

# ─── ToolCall ───


class TestToolCall:
    def test_creation(self):
        tc = ToolCall(id="c1", name="time_get", arguments={"user_id": "test"})
        assert tc.id == "c1"
        assert tc.name == "time_get"
        assert tc.arguments == {"user_id": "test"}

    def test_default_arguments(self):
        tc = ToolCall(id="c1", name="time_get")
        assert tc.arguments == {}

    def test_to_openai(self):
        tc = ToolCall(id="c1", name="time_get", arguments={"tz": "UTC"})
        result = tc.to_openai()
        assert result["id"] == "c1"
        assert result["type"] == "function"
        assert result["function"]["name"] == "time_get"
        args = json.loads(result["function"]["arguments"])
        assert args == {"tz": "UTC"}

    def test_from_openai(self):
        data = {
            "id": "c1",
            "type": "function",
            "function": {"name": "time_get", "arguments": '{"tz": "UTC"}'},
        }
        tc = ToolCall.from_openai(data)
        assert tc.id == "c1"
        assert tc.name == "time_get"
        assert tc.arguments == {"tz": "UTC"}

    def test_from_openai_dict_args(self):
        data = {
            "id": "c1",
            "type": "function",
            "function": {"name": "time_get", "arguments": {"tz": "UTC"}},
        }
        tc = ToolCall.from_openai(data)
        assert tc.arguments == {"tz": "UTC"}

    def test_to_anthropic(self):
        tc = ToolCall(id="c1", name="time_get", arguments={"tz": "UTC"})
        result = tc.to_anthropic()
        assert result["type"] == "tool_use"
        assert result["id"] == "c1"
        assert result["name"] == "time_get"
        assert result["input"] == {"tz": "UTC"}

    def test_from_anthropic(self):
        data = {"type": "tool_use", "id": "c1", "name": "time_get", "input": {"tz": "UTC"}}
        tc = ToolCall.from_anthropic(data)
        assert tc.id == "c1"
        assert tc.name == "time_get"
        assert tc.arguments == {"tz": "UTC"}

    def test_roundtrip_openai(self):
        tc = ToolCall(id="c1", name="email_send", arguments={"to": "a@b.com"})
        assert ToolCall.from_openai(tc.to_openai()) == tc

    def test_roundtrip_anthropic(self):
        tc = ToolCall(id="c1", name="email_send", arguments={"to": "a@b.com"})
        assert ToolCall.from_anthropic(tc.to_anthropic()) == tc


# ─── Message ───


class TestMessage:
    def test_system_convenience(self):
        msg = Message.system("You are helpful")
        assert msg.role == "system"
        assert msg.content == "You are helpful"

    def test_user_convenience(self):
        msg = Message.user("Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_assistant_convenience(self):
        msg = Message.assistant("Hi there")
        assert msg.role == "assistant"
        assert msg.content == "Hi there"
        assert msg.tool_calls == []

    def test_assistant_with_tool_calls(self):
        tc = ToolCall(id="c1", name="time_get", arguments={})
        msg = Message.assistant("Checking time", tool_calls=[tc])
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "time_get"

    def test_tool_result_convenience(self):
        msg = Message.tool_result("call_1", "3pm", name="time_get")
        assert msg.role == "tool"
        assert msg.content == "3pm"
        assert msg.tool_call_id == "call_1"
        assert msg.name == "time_get"

    def test_tool_result_without_name(self):
        msg = Message.tool_result("call_1", "result")
        assert msg.name is None

    def test_default_values(self):
        msg = Message(role="user", content="hi")
        assert msg.tool_calls == []
        assert msg.tool_call_id is None
        assert msg.name is None

    def test_serialization(self):
        msg = Message(role="user", content="hello")
        data = msg.model_dump()
        assert data["role"] == "user"
        assert data["content"] == "hello"

    def test_internal_provenance_is_typed_but_not_serialized(self):
        msg = Message(
            role="user",
            content="hello",
            source="history",
            storage_id="message-1",
            storage_ts="2026-08-03T00:00:00+00:00",
            storage_session_id="chat-1",
        )

        assert msg.storage_id == "message-1"
        assert msg.storage_session_id == "chat-1"
        assert "storage_id" not in msg.model_dump()
        assert "source" not in msg.model_dump()

    def test_deserialization(self):
        data = {
            "role": "assistant",
            "content": "hi",
            "tool_calls": [],
            "tool_call_id": None,
            "name": None,
        }
        msg = Message(**data)
        assert msg.role == "assistant"

    def test_list_content(self):
        msg = Message(role="user", content=[{"type": "text", "text": "see image"}])
        assert isinstance(msg.content, list)


class TestMessageOpenAIFormat:
    def test_user_to_openai(self):
        msg = Message.user("Hello")
        result = msg.to_openai()
        assert result["role"] == "user"
        assert result["content"] == "Hello"

    def test_assistant_with_tool_calls_to_openai(self):
        tc = ToolCall(id="c1", name="time_get", arguments={"tz": "UTC"})
        msg = Message.assistant("Checking", tool_calls=[tc])
        result = msg.to_openai()
        assert result["role"] == "assistant"
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["function"]["name"] == "time_get"

    def test_tool_result_to_openai(self):
        msg = Message.tool_result("c1", "3pm")
        result = msg.to_openai()
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "c1"

    def test_from_openai_user(self):
        data = {"role": "user", "content": "Hello"}
        msg = Message.from_openai(data)
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_from_openai_assistant_with_tool_calls(self):
        data = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "time_get", "arguments": "{}"},
                },
            ],
        }
        msg = Message.from_openai(data)
        assert msg.role == "assistant"
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "time_get"

    def test_roundtrip_openai(self):
        msgs = [
            Message.system("Be helpful"),
            Message.user("What time is it?"),
            Message.assistant("", tool_calls=[ToolCall(id="c1", name="time_get", arguments={})]),
            Message.tool_result("c1", "3pm", name="time_get"),
        ]
        for msg in msgs:
            restored = Message.from_openai(msg.to_openai())
            assert restored.role == msg.role


class TestMessageAnthropicFormat:
    def test_system_to_anthropic(self):
        msg = Message.system("You are helpful")
        result = msg.to_anthropic()
        assert result["type"] == "text"
        assert result["text"] == "You are helpful"

    def test_user_to_anthropic(self):
        msg = Message.user("Hello")
        result = msg.to_anthropic()
        assert result["role"] == "user"
        assert result["content"] == "Hello"

    def test_assistant_with_tool_calls_to_anthropic(self):
        tc = ToolCall(id="c1", name="time_get", arguments={})
        msg = Message.assistant("Checking", tool_calls=[tc])
        result = msg.to_anthropic()
        assert result["role"] == "assistant"
        content = result["content"]
        text_blocks = [b for b in content if b["type"] == "text"]
        tool_blocks = [b for b in content if b["type"] == "tool_use"]
        assert len(text_blocks) == 1
        assert len(tool_blocks) == 1

    def test_from_anthropic_text_block(self):
        block = {"type": "text", "text": "Hello"}
        msg = Message.from_anthropic_block(block)
        assert msg is not None
        assert msg.role == "assistant"
        assert msg.content == "Hello"

    def test_from_anthropic_tool_use_block(self):
        block = {"type": "tool_use", "id": "c1", "name": "time_get", "input": {}}
        msg = Message.from_anthropic_block(block)
        assert msg is not None
        assert msg.role == "assistant"
        assert len(msg.tool_calls) == 1

    def test_from_anthropic_unknown_block(self):
        msg = Message.from_anthropic_block({"type": "image", "source": {}})
        assert msg is None


# ─── StreamChunk ───


class TestStreamChunk:
    def test_ai_token(self):
        chunk = StreamChunk.ai_token("Hello")
        assert chunk.type == "ai_token"
        assert chunk.content == "Hello"

    def test_tool_start(self):
        chunk = StreamChunk.tool_start("time_get", "c1", {"tz": "UTC"})
        assert chunk.type == "tool_start"
        assert chunk.tool == "time_get"
        assert chunk.call_id == "c1"

    def test_tool_end(self):
        chunk = StreamChunk.tool_end("time_get", "c1", "3pm")
        assert chunk.type == "tool_end"
        assert chunk.result_preview == "3pm"

    def test_interrupt(self):
        chunk = StreamChunk.interrupt("files_delete", "c1", {"path": "/x"})
        assert chunk.type == "interrupt"
        assert chunk.tool == "files_delete"

    def test_reasoning(self):
        chunk = StreamChunk.reasoning("Let me think...")
        assert chunk.type == "reasoning"
        assert chunk.content == "Let me think..."

    def test_done(self):
        chunk = StreamChunk.done("3pm today", [{"name": "time_get"}])
        assert chunk.type == "done"
        assert chunk.tool_calls == [{"name": "time_get"}]

    def test_error(self):
        chunk = StreamChunk.error("Connection failed")
        assert chunk.type == "error"
        assert chunk.content == "Connection failed"

    def test_to_ws_message_ai_token(self):
        chunk = StreamChunk.ai_token("Hello")
        ws = chunk.to_ws_message()
        assert ws["type"] == "ai_token"
        assert ws["content"] == "Hello"

    def test_to_ws_message_tool_start(self):
        chunk = StreamChunk.tool_start("time_get", "c1")
        ws = chunk.to_ws_message()
        assert ws["type"] == "tool_start"
        assert ws["tool"] == "time_get"
        assert ws["call_id"] == "c1"

    def test_to_ws_message_done(self):
        chunk = StreamChunk.done("result")
        ws = chunk.to_ws_message()
        assert ws["type"] == "done"
        assert ws["response"] == "result"

    def test_to_ws_message_error(self):
        chunk = StreamChunk.error("fail")
        ws = chunk.to_ws_message()
        assert ws["type"] == "error"
        assert ws["message"] == "fail"
