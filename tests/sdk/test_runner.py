"""Tests for SDK runner (create_sdk_loop, run_sdk_agent, etc.)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.sdk.messages import Message, StreamChunk
from src.sdk.state import AgentState


@pytest.mark.asyncio
async def test_create_sdk_loop_wires_on_summarize():
    """Verify SummarizationMiddleware gets on_summarize callback when enabled."""
    from src.sdk.runner import create_sdk_loop

    with (
        patch("src.sdk.runner.get_settings") as mock_settings,
        patch("src.sdk.runner.create_model_from_config") as mock_create_provider,
        patch("src.sdk.runner.get_native_tools", return_value=[]),
        patch("src.sdk.runner._get_system_prompt", return_value="You are a test assistant."),
        patch("src.storage.messages.get_message_store") as mock_get_store,
    ):
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = True
        settings.memory.summarization.trigger_tokens = 10
        settings.memory.summarization.keep_tokens = 5
        settings.memory.summarization.model = "ollama:test-model"
        settings.agent.model = "ollama:test-model"

        mock_provider = AsyncMock()
        mock_provider.model = "ollama:test-model"
        mock_create_provider.return_value = mock_provider

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store

        loop = await create_sdk_loop(user_id="test_user")

        summarization_mw = None
        for mw in loop.middlewares:
            if mw.__class__.__name__ == "SummarizationMiddleware":
                summarization_mw = mw
                break

        assert summarization_mw is not None
        assert summarization_mw._on_summarize is not None

        summarization_mw._generate_summary = AsyncMock(
            return_value=(
                "This is a test summary of the conversation. It covers the key topics discussed "
                "including user preferences, decisions made, and action items identified. "
                "The user asked about various subjects and the assistant provided helpful responses. "
                "Several important facts were established during this exchange. "
                "The conversation covered multiple topics and reached several conclusions. "
                "Key points included the user's preferences for concise answers and structured responses. "
                "The assistant demonstrated the ability to handle complex queries. "
                "Overall this was a productive exchange that achieved its objectives. "
                "The summary captures all essential information for future reference. "
                "Nothing important was omitted from this conversation summary."
            )
        )

        msgs = [Message.user(f"Message number {i} about various topics.") for i in range(20)]
        state = AgentState(messages=msgs)
        result = await summarization_mw.abefore_model(state)

        assert result is not None, "Summarization should have triggered"

        mock_get_store.assert_called_with("test_user")
        mock_store.add_summary_message.assert_called_once()
        call_arg = mock_store.add_summary_message.call_args[0][0]
        assert "test summary of the conversation" in call_arg


@pytest.mark.asyncio
async def test_create_sdk_loop_no_on_summarize_when_disabled():
    """Verify SummarizationMiddleware has no on_summarize when disabled."""
    from src.sdk.runner import create_sdk_loop

    with (
        patch("src.sdk.runner.get_settings") as mock_settings,
        patch("src.sdk.runner.create_model_from_config") as mock_create_provider,
        patch("src.sdk.runner.get_native_tools", return_value=[]),
        patch("src.sdk.runner._get_system_prompt", return_value="You are a test assistant."),
    ):
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = False
        settings.agent.model = "ollama:test-model"

        mock_provider = AsyncMock()
        mock_provider.model = "ollama:test-model"
        mock_create_provider.return_value = mock_provider

        loop = await create_sdk_loop(user_id="test_user")

        summarization_mw = None
        for mw in loop.middlewares:
            if mw.__class__.__name__ == "SummarizationMiddleware":
                summarization_mw = mw
                break

        assert summarization_mw is None


@pytest.mark.asyncio
async def test_run_sdk_agent_stream_triggers_summarization():
    """Summarization fires during run_sdk_agent_stream and persists summary."""
    from src.sdk.runner import get_sdk_loop, reset_sdk_loop, run_sdk_agent_stream

    reset_sdk_loop("test_stream_user")

    class MockStreamProvider:
        model = "test-model"

        async def chat_stream(self, messages, tools=None, model=None, provider_options=None):
            yield StreamChunk.text_delta("ok")

    with (
        patch("src.sdk.runner.get_settings") as mock_settings,
        patch("src.sdk.runner.create_model_from_config") as mock_create_provider,
        patch("src.sdk.runner.get_native_tools", return_value=[]),
        patch("src.sdk.runner._get_system_prompt", return_value="You are test assistant."),
        patch("src.storage.messages.get_message_store") as mock_get_store,
    ):
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = True
        settings.memory.summarization.trigger_tokens = 10
        settings.memory.summarization.keep_tokens = 5
        settings.memory.summarization.model = "ollama:test-model"
        settings.agent.model = "ollama:test-model"

        mock_create_provider.return_value = MockStreamProvider()

        mock_store = MagicMock()
        mock_get_store.return_value = mock_store

        # Pre-create the loop so we can mock _generate_summary on the middleware
        loop = await get_sdk_loop(user_id="test_stream_user")

        summarization_mw = None
        for mw in loop.middlewares:
            if mw.__class__.__name__ == "SummarizationMiddleware":
                summarization_mw = mw
                break

        assert summarization_mw is not None
        summary_text = (
            "This is a test summary of the conversation. It covers the key topics discussed "
            "including user preferences, decisions made, and action items identified. "
            "The user asked about various subjects and the assistant provided helpful responses. "
            "Several important facts were established during this exchange. "
            "The conversation covered multiple topics and reached several conclusions. "
            "Key points included the user's preferences for concise answers and structured responses. "
            "The assistant demonstrated the ability to handle complex queries. "
            "Overall this was a productive exchange that achieved its objectives. "
            "The summary captures all essential information for future reference. "
            "Nothing important was omitted from this conversation summary."
        )
        summarization_mw._generate_summary = AsyncMock(return_value=summary_text)

        long_msgs = [Message.user(f"Message number {i} about various topics.") for i in range(30)]

        chunks = []
        async for chunk in run_sdk_agent_stream(
            user_id="test_stream_user",
            messages=long_msgs,
        ):
            chunks.append(chunk)

        assert mock_store.add_summary_message.called, (
            "add_summary_message should have been called when summarization triggered"
        )

