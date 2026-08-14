"""Agent loop conformance tests (non-HTTP).

These tests verify agent behavior through direct Python calls,
not through the HTTP API. For API-level tests, see tests/api/test_agent_loop.py.
"""




class TestAgentLoopBasic:
    """Basic agent loop behavior that must be consistent."""

    def test_run_config_defaults(self):
        """AgentLoop RunConfig must have sensible defaults."""
        from src.sdk.loop import RunConfig

        config = RunConfig()
        assert config.max_llm_calls > 0
        assert config.max_iterations > 0

    def test_run_config_custom(self):
        """RunConfig must accept custom limits."""
        from src.sdk.loop import RunConfig

        config = RunConfig(max_llm_calls=10, max_iterations=5)
        assert config.max_llm_calls == 10
        assert config.max_iterations == 5

    def test_agent_loop_constructor(self):
        """AgentLoop must be constructable with provider and tools."""
        from unittest.mock import MagicMock

        from src.sdk.loop import AgentLoop

        # Real providers expose string model + provider_id attributes; the
        # loop infers its canonical model_id from them.
        provider = MagicMock(model="gpt-4o", provider_id="openai")
        loop = AgentLoop(provider=provider, tools=[], system_prompt="test")
        assert loop is not None
        assert loop.model_id == "openai:gpt-4o"


class TestAgentLoopWSProtocol:
    """WebSocket protocol must be self-consistent with HTTP API."""

    def test_ws_protocol_covers_all_event_types(self):
        """WS protocol must define types for all agent events."""
        from src.http.ws_protocol import CLIENT_MESSAGE_TYPES, SERVER_MESSAGE_TYPES

        expected_server_types = {
            "ai_token",
            "tool_start",
            "tool_end",
            "interrupt",
            "done",
            "error",
            "pong",
        }
        expected_client_types = {
            "user_message",
            "approve",
            "reject",
            "edit_and_approve",
            "cancel",
            "ping",
        }

        actual_server = set(SERVER_MESSAGE_TYPES.keys())
        actual_client = set(CLIENT_MESSAGE_TYPES.keys())

        assert expected_server_types.issubset(actual_server), (
            f"Missing: {expected_server_types - actual_server}"
        )
        assert expected_client_types.issubset(actual_client), (
            f"Missing: {expected_client_types - actual_client}"
        )

    def test_interrupt_message_has_allowed_actions(self):
        """Interrupt messages must specify allowed actions for HITL."""
        from src.http.ws_protocol import InterruptMessage

        msg = InterruptMessage(call_id="c1", tool="files_delete", args={"path": "/x"})
        assert "approve" in msg.allowed_actions
        assert "reject" in msg.allowed_actions

    def test_done_message_can_include_tool_calls(self):
        """Done message must be able to include tool call info."""
        from src.http.ws_protocol import DoneMessage

        msg = DoneMessage(
            response="Here are the results", tool_calls=[{"name": "time_get", "call_id": "c1"}]
        )
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0]["name"] == "time_get"
