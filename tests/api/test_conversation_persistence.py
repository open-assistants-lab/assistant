from src.http.conversation_persistence import (
    persist_assistant_message,
    persist_reasoning_message,
    persist_tool_message,
)


class FakeConversation:
    def __init__(self):
        self.calls = []

    def add_message(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "msg-1"


def test_persist_assistant_message_uses_session_id_without_workspace_metadata():
    conversation = FakeConversation()

    msg_id = persist_assistant_message(
        conversation,
        "hello",
        session_id="chat-1",
        metadata={"stream": True},
    )

    assert msg_id == "msg-1"
    assert conversation.calls == [
        (("assistant", "hello"), {"metadata": {"stream": True}, "session_id": "chat-1"})
    ]


def test_persist_reasoning_message_uses_session_id_without_workspace_metadata():
    conversation = FakeConversation()

    persist_reasoning_message(conversation, "thinking", session_id="chat-1")

    assert conversation.calls == [
        (("reasoning", "thinking"), {"metadata": {}, "session_id": "chat-1"})
    ]


def test_persist_tool_message_uses_session_id_without_workspace_metadata():
    conversation = FakeConversation()

    persist_tool_message(
        conversation,
        "result",
        session_id="chat-1",
        tool_name="time_get",
        tool_call_id="call-1",
    )

    assert conversation.calls == [
        (
            ("tool", "result"),
            {
                "metadata": {"tool_name": "time_get", "tool_call_id": "call-1"},
                "session_id": "chat-1",
            },
        )
    ]


def test_persist_collected_stream_state_persists_collected_content_without_fallback():
    from src.http.routers.conversation import _persist_collected_stream_state

    conversation = FakeConversation()

    _persist_collected_stream_state(
        conversation,
        session_id="chat-1",
        ai_content_parts=["partial"],
        reasoning_parts=["thinking"],
        tool_metadata_list=[{"tool_name": "time_get", "tool_call_id": "call-1"}],
        tool_results={"call-1": "noon"},
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
