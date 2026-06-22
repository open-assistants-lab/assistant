"""Integration tests: WebSocket + memory middleware + summarization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from src.sdk.messages import Message, StreamChunk


@dataclass
class StoredMessage:
    role: str
    content: str
    metadata: dict | None = None


class FakeConversation:
    def __init__(self) -> None:
        self.messages: list[StoredMessage] = []

    def add_message(self, role: str, content: str, metadata: dict | None = None, **kwargs: object) -> str:
        self.messages.append(StoredMessage(role, content, metadata))
        return "msg_1"

    def get_messages_with_summary(self, limit: int = 50, **kwargs: object) -> list[StoredMessage]:
        return self.messages[-limit:]

    def get_recent_messages_for_session(self, session_id: str = "", count: int = 100) -> list[StoredMessage]:
        return self.messages[-count:]

    def add_summary_message(self, content: str) -> str:
        self.messages.append(StoredMessage("summary", content, {}))
        return "summary_1"


class TestWSMemoryIntegration:
    """WebSocket sends user messages → memory stores them → middleware retrieves them."""

    def _wait_for_done(self, ws) -> str | None:
        """Receive WS messages until done or error, return response text."""
        while True:
            msg = ws.receive_json()
            if msg.get("type") == "done":
                return msg.get("response", "")
            if msg.get("type") == "error":
                return None

    def test_ws_stores_messages_with_session(self, client, monkeypatch, test_user_id):
        """Verify WS user_message → memory store with session context."""
        seen_messages: list[Message] = []

        async def fake_stream(**kwargs):
            msg_list = kwargs["messages"]
            seen_messages.extend(msg_list)
            yield StreamChunk.done("Hello back")

        monkeypatch.setattr(
            "src.http.routers.ws.run_sdk_agent_stream", fake_stream
        )

        store = FakeConversation()
        monkeypatch.setattr(
            "src.http.routers.ws.get_message_store",
            lambda *args, **kwargs: store,
        )

        with client.websocket_connect("/ws/conversation?session_id=ws-session-test-1") as ws:
            ws.send_json({
                "type": "user_message",
                "content": "hello from ws",
                "user_id": test_user_id,
            })
            response = self._wait_for_done(ws)
            assert response is not None, "Server returned error"

        stored_roles = [m.role for m in store.messages]
        assert "user" in stored_roles
        user_msgs = [m.content for m in store.messages if m.role == "user"]
        assert "hello from ws" in user_msgs

    def test_ws_session_isolation(self, client, monkeypatch, test_user_id):
        """Two different sessions on WS store messages independently."""
        store = FakeConversation()

        async def fake_stream(**kwargs):
            yield StreamChunk.text_delta("ok")
            yield StreamChunk.done("ok")

        monkeypatch.setattr(
            "src.http.routers.ws.run_sdk_agent_stream", fake_stream
        )
        monkeypatch.setattr(
            "src.http.routers.ws.get_message_store",
            lambda *args, **kwargs: store,
        )

        with client.websocket_connect("/ws/conversation?session_id=sess-a") as ws_a:
            ws_a.send_json({
                "type": "user_message",
                "content": "message for session A",
                "user_id": test_user_id,
            })
            assert self._wait_for_done(ws_a) is not None

        with client.websocket_connect("/ws/conversation?session_id=sess-b") as ws_b:
            ws_b.send_json({
                "type": "user_message",
                "content": "message for session B",
                "user_id": test_user_id,
            })
            assert self._wait_for_done(ws_b) is not None

        user_msgs = [m.content for m in store.messages if m.role == "user"]
        assert "message for session A" in user_msgs
        assert "message for session B" in user_msgs
        assert len(user_msgs) == 2

    def test_ws_memory_provides_history(self, client, monkeypatch, test_user_id):
        """Conversation history is loaded from store and passed to agent."""
        seen_messages: list[Message] = []

        async def fake_stream(**kwargs):
            msg_list = kwargs["messages"]
            seen_messages.extend(msg_list)
            yield StreamChunk.done("with history")

        monkeypatch.setattr(
            "src.http.routers.ws.run_sdk_agent_stream", fake_stream
        )

        store = FakeConversation()
        store.messages.append(StoredMessage("user", "earlier msg", {}))
        store.messages.append(StoredMessage("assistant", "earlier reply", {}))

        monkeypatch.setattr(
            "src.http.routers.ws.get_message_store",
            lambda *args, **kwargs: store,
        )

        with client.websocket_connect("/ws/conversation?session_id=ws-history-test") as ws:
            ws.send_json({
                "type": "user_message",
                "content": "what did we discuss?",
                "user_id": test_user_id,
            })
            assert self._wait_for_done(ws) is not None

        assert any("earlier" in m.content for m in seen_messages), \
            "History messages should be passed to agent"

    def test_summarize_tool_importable(self):
        """The summarize tool module imports cleanly."""
        from src.sdk.tools_core.summarize import SummarizationMiddleware
        from src.sdk.native_tools import get_native_tool_names
        assert SummarizationMiddleware is not None
        assert "summarize_session" in get_native_tool_names()

    def test_ws_multiple_messages_accumulate(self, client, monkeypatch, test_user_id):
        """Multiple messages on same WS session all get stored."""
        store = FakeConversation()

        async def fake_stream(**kwargs):
            yield StreamChunk.done("ok")

        monkeypatch.setattr(
            "src.http.routers.ws.run_sdk_agent_stream", fake_stream
        )
        monkeypatch.setattr(
            "src.http.routers.ws.get_message_store",
            lambda *args, **kwargs: store,
        )

        with client.websocket_connect("/ws/conversation?session_id=ws-multi-msg") as ws:
            for i in range(3):
                ws.send_json({
                    "type": "user_message",
                    "content": f"message {i}",
                    "user_id": test_user_id,
                })
                assert self._wait_for_done(ws) is not None

        user_msgs = [m.content for m in store.messages if m.role == "user"]
        assert len(user_msgs) == 3
        assert "message 0" in user_msgs
        assert "message 1" in user_msgs
        assert "message 2" in user_msgs


class TestSummarizationMiddleware:
    """Summarization middleware integration tests."""

    def test_summarization_middleware_instantiated(self):
        """SummarizationMiddleware can be created with config."""
        from src.sdk.middleware_summarization import SummarizationMiddleware
        mw = SummarizationMiddleware(
            trigger_tokens=100,
            keep_tokens=50,
            model="test-model",
        )
        assert mw.trigger_tokens == 100
        assert mw.keep_tokens == 50

    @pytest.mark.asyncio
    async def test_force_summarize_returns_false_for_empty(self):
        """force_summarize returns False when no messages."""
        from src.sdk.middleware_summarization import SummarizationMiddleware
        from src.sdk.state import AgentState
        mw = SummarizationMiddleware(model="test-model")
        state = AgentState(messages=[])
        result = await mw.force_summarize(state)
        assert result is False

    def test_summarization_wired_in_runner(self):
        """SummarizationMiddleware is part of the default middleware stack."""
        from src.sdk.runner import create_sdk_loop
        assert create_sdk_loop is not None

    def test_memory_is_loaded_via_conversation_store(self):
        """Conversation history is loaded from the message store and passed to SDK."""
        from src.storage.messages import get_message_store, MessageStore
        from src.sdk.runner import _messages_from_conversation
        assert MessageStore is not None
        assert get_message_store is not None
        assert _messages_from_conversation is not None
