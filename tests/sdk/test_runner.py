"""Tests for SDK runner (create_sdk_loop, run_sdk_agent, etc.)."""

from __future__ import annotations

import builtins
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.sdk.messages import Message, StreamChunk
from src.sdk.state import AgentState
from src.storage.messages import Message as StoredMessage


class _FakeIndex:
    def count(self):
        return 0

    def clear(self):
        pass

    def index_tool(self, *args, **kwargs):
        pass


@pytest.fixture
def loop_factory(monkeypatch):
    from src.sdk import runner

    settings = MagicMock()
    settings.memory.summarization.enabled = True
    settings.memory.summarization.get_trigger.return_value = ("messages", 2)
    settings.memory.summarization.get_keep.return_value = ("messages", 1)
    settings.memory.summarization.model = None
    settings.memory.summarization.trim_tokens_to_summarize = 4000
    settings.memory.summarization.prompt_file = None
    settings.verification.enabled = False
    settings.langfuse.enabled = False
    monkeypatch.setattr(runner, "get_settings", lambda: settings)
    monkeypatch.setattr(runner, "get_native_tools", lambda: [])
    monkeypatch.setattr(runner, "_seed_default_workspace", lambda: None)
    monkeypatch.setattr(runner, "_get_system_prompt", lambda *args, **kwargs: "prompt")
    monkeypatch.setattr(
        "src.config.user_settings_service.load_saved_user_settings", lambda user_id: None
    )
    monkeypatch.setattr("src.sdk.tool_index.get_or_create_index", lambda *args, **kwargs: _FakeIndex())

    async def create(
        session_id=None,
        *,
        provider_id="openai",
        provider_model="gpt-4.1",
        user_id="test_user",
    ):
        provider = AsyncMock()
        provider.provider_id = provider_id
        provider.model = provider_model
        monkeypatch.setattr(runner, "get_cached_model_provider", lambda *args, **kwargs: provider)
        return await runner.create_sdk_loop(user_id=user_id, session_id=session_id)

    return create


def _artifact(*, eligible=True):
    from src.sdk.compression import CompressionArtifact, CompressionMessage

    summary = Message(role="user", content="summary", source="summarization_middleware")
    return CompressionArtifact(
        summary="summary",
        replacement_messages=(CompressionMessage.from_message(summary),),
        summarized_message_count=2,
        preserved_message_count=1,
        summarized_message_ids=("m1", "m2") if eligible else (),
        preserved_message_ids=("m3",),
        persistence_eligible=eligible,
    )


def _context(session_id="chat-1"):
    from src.sdk.compression import CompressionContext, CompressionReason

    return CompressionContext(
        session_id=session_id,
        model="openai:gpt-4.1",
        attempt=1,
        llm_call_index=1,
        reason=CompressionReason.THRESHOLD,
    )


@pytest.mark.asyncio
async def test_create_sdk_loop_flow_identity_carries_session(loop_factory):
    """The loop's flow identity must carry the session it was created for.

    Compression contexts derive their session_id from _flow_identity(), and
    _persist_summary rejects a mismatch — so a loop created via
    create_sdk_loop (the RunService path) with a real session must report
    that session. Previously _flow_session_id was only set by the legacy
    run_sdk_agent/run_sdk_agent_stream, leaving 'default' and silently
    dropping every summarization persist."""
    loop = await loop_factory("chat-42")
    assert loop._flow_session_id == "chat-42"
    attempt, session_id = loop._flow_identity()
    assert session_id == "chat-42"


@pytest.mark.asyncio
async def test_create_sdk_loop_wires_typed_summary_sink(loop_factory, monkeypatch):
    from src.sdk.compression import PersistenceStatus

    store = MagicMock()
    store.add_summary_message.return_value = "summary-1"
    monkeypatch.setattr("src.storage.messages.get_message_store", lambda user_id: store)
    loop = await loop_factory("chat-1")
    middleware = loop.middlewares[0]

    result = await middleware._summary_sink(_context(), _artifact())

    assert result.status is PersistenceStatus.SUCCEEDED
    assert result.summary_id == "summary-1"
    store.add_summary_message.assert_called_once_with(
        "summary",
        session_id="chat-1",
        metadata={
            "source": "summarization_middleware",
            "compression_reason": "threshold",
            "summarized_message_ids": ["m1", "m2"],
            "preserved_message_ids": ["m3"],
        },
    )


@pytest.mark.asyncio
async def test_summary_sink_rejects_session_mismatch(loop_factory, monkeypatch):
    from src.sdk.compression import PersistenceStatus

    store = MagicMock()
    monkeypatch.setattr("src.storage.messages.get_message_store", lambda user_id: store)
    loop = await loop_factory("chat-1")

    result = await loop.middlewares[0]._summary_sink(_context("chat-2"), _artifact())

    assert result.status is PersistenceStatus.FAILED
    store.add_summary_message.assert_not_called()


@pytest.mark.asyncio
async def test_summary_sink_skips_ineligible_artifact(loop_factory, monkeypatch):
    from src.sdk.compression import PersistenceStatus

    store = MagicMock()
    monkeypatch.setattr("src.storage.messages.get_message_store", lambda user_id: store)
    loop = await loop_factory("chat-1")

    result = await loop.middlewares[0]._summary_sink(_context(), _artifact(eligible=False))

    assert result.status is PersistenceStatus.NOT_REQUESTED
    store.add_summary_message.assert_not_called()


@pytest.mark.asyncio
async def test_summary_sink_returns_failed_for_empty_storage_id(loop_factory, monkeypatch):
    from src.sdk.compression import PersistenceStatus

    store = MagicMock()
    store.add_summary_message.return_value = ""
    monkeypatch.setattr("src.storage.messages.get_message_store", lambda user_id: store)
    loop = await loop_factory("chat-1")

    result = await loop.middlewares[0]._summary_sink(_context(), _artifact())

    assert result.status is PersistenceStatus.FAILED


@pytest.mark.asyncio
async def test_summary_sink_contains_store_exception(loop_factory, monkeypatch):
    from src.sdk.compression import PersistenceStatus

    store = MagicMock()
    store.add_summary_message.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr("src.storage.messages.get_message_store", lambda user_id: store)
    loop = await loop_factory("chat-1")

    result = await loop.middlewares[0]._summary_sink(_context(), _artifact())

    assert result.status is PersistenceStatus.FAILED


@pytest.mark.asyncio
async def test_two_loop_sinks_persist_to_owning_sessions(loop_factory, monkeypatch):
    store = MagicMock()
    store.add_summary_message.side_effect = ["summary-a", "summary-b"]
    monkeypatch.setattr("src.storage.messages.get_message_store", lambda user_id: store)
    loop_a = await loop_factory("chat-a")
    loop_b = await loop_factory("chat-b")

    result_a = await loop_a.middlewares[0]._summary_sink(_context("chat-a"), _artifact())
    result_b = await loop_b.middlewares[0]._summary_sink(_context("chat-b"), _artifact())

    assert (result_a.summary_id, result_b.summary_id) == ("summary-a", "summary-b")
    assert [call.kwargs["session_id"] for call in store.add_summary_message.call_args_list] == [
        "chat-a",
        "chat-b",
    ]


@pytest.mark.asyncio
async def test_persisted_summary_id_is_applied_by_middleware(loop_factory, monkeypatch):
    from src.sdk.compression import CompressionContext, CompressionReason
    from src.sdk.run_models import UsageAggregate

    store = MagicMock()
    store.add_summary_message.return_value = "stored-summary"
    monkeypatch.setattr("src.storage.messages.get_message_store", lambda user_id: store)
    loop = await loop_factory("chat-1")
    middleware = loop.middlewares[0]
    middleware._acreate_summary = AsyncMock(return_value=("summary", UsageAggregate()))
    messages = [
        Message(role="user", content="old", storage_id="m1"),
        Message(role="assistant", content="reply", storage_id="m2"),
        Message(role="user", content="recent", storage_id="m3"),
    ]
    context = CompressionContext(
        session_id="chat-1",
        model="openai:gpt-4.1",
        attempt=1,
        llm_call_index=1,
        reason=CompressionReason.MANUAL,
    )

    result = await middleware.force_summarize(AgentState(messages=messages), context)

    assert result.artifact is not None
    assert result.artifact.persisted_summary_id == "stored-summary"
    assert result.artifact.replacement_messages[0].storage_id == "stored-summary"


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", [None, "", "   "])
async def test_get_sdk_loop_forwards_normalized_default_session(monkeypatch, session_id):
    from src.sdk import runner

    runner._loop_cache.clear()
    create = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, "create_sdk_loop", create)

    await runner.get_sdk_loop("u", session_id=session_id)

    assert create.call_args.kwargs["session_id"] == "default"


@pytest.mark.asyncio
async def test_get_sdk_loop_keeps_sessions_in_separate_cache_entries(monkeypatch):
    from src.sdk import runner

    runner._loop_cache.clear()
    create = AsyncMock(side_effect=[object(), object()])
    monkeypatch.setattr(runner, "create_sdk_loop", create)

    first = await runner.get_sdk_loop("u", session_id="chat-1")
    second = await runner.get_sdk_loop("u", session_id="chat-2")

    assert first is not second
    assert [call.kwargs["session_id"] for call in create.call_args_list] == ["chat-1", "chat-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_id", "provider_model", "expected"),
    [
        ("openai", "gpt-4.1", "openai:gpt-4.1"),
        ("ollama", "qwen3:8b", "ollama:qwen3:8b"),
        ("openrouter", "anthropic/claude-sonnet-4", "openrouter:anthropic/claude-sonnet-4"),
    ],
)
async def test_create_sdk_loop_uses_canonical_provider_model_id(
    loop_factory, provider_id, provider_model, expected
):
    loop = await loop_factory(
        "chat-1", provider_id=provider_id, provider_model=provider_model
    )

    assert loop.model_id == expected


@pytest.mark.asyncio
async def test_create_sdk_loop_uses_actual_local_ollama_identity(loop_factory, monkeypatch):
    from src.sdk import runner
    from src.sdk.providers.factory import get_cached_model_provider
    from src.sdk.providers.openai import OpenAIProvider

    monkeypatch.setattr(runner, "get_cached_model_provider", get_cached_model_provider)
    monkeypatch.setattr(
        "src.sdk.providers.factory.create_provider_from_registry_model",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "src.sdk.providers.factory._load_stored_key", lambda *args, **kwargs: None
    )

    loop = await runner.create_sdk_loop(
        user_id="test_user",
        model="ollama:qwen3:8b",
        session_id="chat-1",
    )

    assert isinstance(loop.provider, OpenAIProvider)
    assert loop.provider.provider_id == "ollama"
    assert loop.model_id == "ollama:qwen3:8b"
    assert loop.middlewares[0].model == "ollama:qwen3:8b"


@pytest.mark.asyncio
async def test_create_sdk_loop_caches_connectkit_discovery(monkeypatch, loop_factory):
    """Two loop creations for the same user within the TTL share one
    ConnectKit discovery and the same bridge instance."""
    from src.sdk import runner

    discover_calls = {"n": 0}

    class FakeConnectorBridge:
        def __init__(self, user_id):
            self.user_id = user_id

        async def discover(self):
            discover_calls["n"] += 1

        def get_tool_definitions(self):
            return []

    monkeypatch.setattr("connectkit.bridge.ConnectKitBridge", FakeConnectorBridge)
    monkeypatch.setattr(runner, "_CONNECTKIT_CACHE_TTL_SECONDS", 3600.0)
    runner.clear_connectkit_cache()

    loop1 = await loop_factory(user_id="ck_test_user")
    loop2 = await loop_factory(user_id="ck_test_user")

    assert discover_calls["n"] == 1
    assert loop1._connectkit_bridge is loop2._connectkit_bridge


@pytest.mark.asyncio
async def test_connectkit_cache_re_discovers_after_ttl(monkeypatch, loop_factory):
    """Once the TTL elapses, discovery runs again for the same user."""
    import time

    from src.sdk import runner

    discover_calls = {"n": 0}

    class FakeConnectorBridge:
        def __init__(self, user_id):
            self.user_id = user_id

        async def discover(self):
            discover_calls["n"] += 1

        def get_tool_definitions(self):
            return []

    monkeypatch.setattr("connectkit.bridge.ConnectKitBridge", FakeConnectorBridge)
    monkeypatch.setattr(runner, "_CONNECTKIT_CACHE_TTL_SECONDS", 60.0)
    runner.clear_connectkit_cache()

    await loop_factory(user_id="ck_test_user")
    assert discover_calls["n"] == 1

    # Age the cached entry past the TTL, then the next call re-discovers.
    runner._connectkit_cache["ck_test_user"] = (time.monotonic() - 61.0, None, [])
    await loop_factory(user_id="ck_test_user")
    assert discover_calls["n"] == 2


@pytest.mark.asyncio
async def test_connectkit_cache_is_per_user(monkeypatch, loop_factory):
    """Different users each get their own discovery (and cache entry)."""
    from src.sdk import runner

    discover_calls = {"n": 0}

    class FakeConnectorBridge:
        def __init__(self, user_id):
            self.user_id = user_id

        async def discover(self):
            discover_calls["n"] += 1

        def get_tool_definitions(self):
            return []

    monkeypatch.setattr("connectkit.bridge.ConnectKitBridge", FakeConnectorBridge)
    monkeypatch.setattr(runner, "_CONNECTKIT_CACHE_TTL_SECONDS", 3600.0)
    runner.clear_connectkit_cache()

    await loop_factory(user_id="ck_user_a")
    await loop_factory(user_id="ck_user_b")
    await loop_factory(user_id="ck_user_a")

    assert discover_calls["n"] == 2


@pytest.mark.asyncio
async def test_connectkit_failure_is_not_cached(monkeypatch, loop_factory):
    """A failed discovery is never cached, so the next call retries."""
    from src.sdk import runner

    attempts = {"n": 0}

    class FlakyConnectorBridge:
        def __init__(self, user_id):
            self.user_id = user_id

        async def discover(self):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("boom")

        def get_tool_definitions(self):
            return []

    monkeypatch.setattr("connectkit.bridge.ConnectKitBridge", FlakyConnectorBridge)
    monkeypatch.setattr(runner, "_CONNECTKIT_CACHE_TTL_SECONDS", 3600.0)
    runner.clear_connectkit_cache()

    loop = await loop_factory(user_id="ck_test_user")
    assert attempts["n"] == 1  # first failure swallowed, loop still created
    assert getattr(loop, "_connectkit_bridge", None) is None

    await loop_factory(user_id="ck_test_user")
    assert attempts["n"] == 2  # not cached → retried


@pytest.mark.asyncio
async def test_connectkit_cache_is_bounded(monkeypatch, loop_factory):
    """The cache evicts least-recently-used users beyond the cap."""
    from src.sdk import runner

    discover_calls = {"n": 0}

    class FakeConnectorBridge:
        def __init__(self, user_id):
            self.user_id = user_id

        async def discover(self):
            discover_calls["n"] += 1

        def get_tool_definitions(self):
            return []

    monkeypatch.setattr("connectkit.bridge.ConnectKitBridge", FakeConnectorBridge)
    monkeypatch.setattr(runner, "_CONNECTKIT_CACHE_TTL_SECONDS", 3600.0)
    monkeypatch.setattr(runner, "_CONNECTKIT_CACHE_MAX_ENTRIES", 3)
    runner.clear_connectkit_cache()

    for i in range(4):
        await loop_factory(user_id=f"ck_user_{i}")
    assert len(runner._connectkit_cache) <= 3

    # The first user was evicted, so its next loop re-discovers; the
    # fourth user's entry is still cached.
    await loop_factory(user_id="ck_user_0")
    assert discover_calls["n"] == 5  # 4 initial + 1 re-discovery


def test_messages_from_conversation_preserves_user_storage_identity():
    from src.sdk.runner import _messages_from_conversation

    ts = datetime(2026, 8, 3, 1, 2, 3, tzinfo=UTC)
    converted = _messages_from_conversation(
        [StoredMessage("user-1", ts, "user", "hello", session_id="chat-1")]
    )

    assert converted[0].storage_id == "user-1"
    assert converted[0].storage_ts == ts.isoformat()
    assert converted[0].storage_session_id == "chat-1"


def test_messages_from_conversation_preserves_persisted_summary_source_and_identity():
    from src.sdk.runner import _messages_from_conversation

    ts = datetime(2026, 8, 3, tzinfo=UTC)
    stored = StoredMessage(
        "summary-1",
        ts,
        "summary",
        "compressed",
        metadata={"source": "persisted_compression"},
        session_id="chat-1",
    )

    converted = _messages_from_conversation([stored])

    assert converted[0].role == "user"
    assert converted[0].source == "persisted_compression"
    assert converted[0].storage_id == "summary-1"
    assert converted[0].storage_session_id == "chat-1"


def test_messages_from_conversation_summary_source_falls_back_to_middleware():
    from src.sdk.runner import _messages_from_conversation

    converted = _messages_from_conversation(
        [StoredMessage("summary-1", datetime.now(UTC), "summary", "compressed", session_id="chat")]
    )

    assert converted[0].source == "summarization_middleware"


def test_messages_from_conversation_preserves_tool_storage_identity():
    from src.sdk.runner import _messages_from_conversation

    stored = StoredMessage(
        "tool-1",
        datetime.now(UTC),
        "tool",
        "result",
        metadata={"tool_name": "time_get", "tool_call_id": "call-1"},
        session_id="chat",
    )

    converted = _messages_from_conversation([stored])

    assert converted[0].storage_id == "tool-1"
    assert converted[0].storage_session_id == "chat"


def test_messages_from_conversation_assistant_identity_is_authoritative_over_reasoning():
    from src.sdk.runner import _messages_from_conversation

    ts = datetime.now(UTC)
    converted = _messages_from_conversation(
        [
            StoredMessage("reason-1", ts, "reasoning", "thinking", session_id="chat"),
            StoredMessage("assistant-1", ts, "assistant", "answer", session_id="chat"),
        ]
    )

    assert converted[0].reasoning == "thinking"
    assert converted[0].storage_id == "assistant-1"
    assert converted[0].storage_session_id == "chat"


@pytest.mark.asyncio
async def test_create_sdk_loop_has_no_summary_middleware_when_disabled():
    from src.sdk.runner import create_sdk_loop

    with (
        patch("src.sdk.runner.get_settings") as mock_settings,
        patch("src.sdk.runner.get_cached_model_provider") as mock_create_provider,
        patch("src.sdk.runner.get_native_tools", return_value=[]),
        patch("src.sdk.runner._seed_default_workspace"),
        patch("src.sdk.runner._get_system_prompt", return_value="You are a test assistant."),
    ):
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = False
        settings.agent.model = "ollama:test-model"

        mock_provider = AsyncMock()
        mock_provider.provider_id = "ollama"
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
async def test_create_sdk_loop_passes_user_id_to_provider_factory():
    """Implicit model lets provider factory use stored per-user default model."""
    from src.sdk.runner import create_sdk_loop

    with (
        patch("src.sdk.runner.get_settings") as mock_settings,
        patch("src.sdk.runner.get_cached_model_provider") as mock_create_provider,
        patch("src.sdk.runner.get_native_tools", return_value=[]),
        patch("src.sdk.runner._seed_default_workspace"),
        patch("src.sdk.runner._get_system_prompt", return_value="You are a test assistant."),
    ):
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = False
        settings.agent.model = "openai:gpt-4.1"
        mock_provider = AsyncMock()
        mock_provider.provider_id = "openai"
        mock_provider.model = "openai:gpt-4.1"
        mock_create_provider.return_value = mock_provider

        await create_sdk_loop(user_id="test_user")

    mock_create_provider.assert_called_once_with(None, provider_keys=None, user_id="test_user")


@pytest.mark.asyncio
async def test_create_sdk_loop_passes_explicit_model_to_provider_factory():
    from src.sdk.runner import create_sdk_loop

    with (
        patch("src.sdk.runner.get_settings") as mock_settings,
        patch("src.sdk.runner.get_cached_model_provider") as mock_create_provider,
        patch("src.sdk.runner.get_native_tools", return_value=[]),
        patch("src.sdk.runner._seed_default_workspace"),
        patch("src.sdk.runner._get_system_prompt", return_value="You are a test assistant."),
    ):
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = False
        settings.agent.model = "ollama:fallback"
        mock_provider = AsyncMock()
        mock_provider.provider_id = "openai"
        mock_provider.model = "openai:gpt-4.1"
        mock_create_provider.return_value = mock_provider

        await create_sdk_loop(user_id="test_user", model="openai:gpt-4.1")

    mock_create_provider.assert_called_once_with(
        "openai:gpt-4.1", provider_keys=None, user_id="test_user"
    )


@pytest.mark.asyncio
async def test_run_sdk_agent_stream_triggers_summarization():
    """Summarization fires during run_sdk_agent_stream and persists summary."""
    from src.sdk.runner import get_sdk_loop, reset_sdk_loop, run_sdk_agent_stream

    reset_sdk_loop("test_stream_user")

    class MockStreamProvider:
        provider_id = "ollama"
        model = "test-model"

        async def chat_stream(self, messages, tools=None, model=None, provider_options=None):
            yield StreamChunk.text_delta("ok")

    with (
        patch("src.sdk.runner.get_settings") as mock_settings,
        patch("src.sdk.runner.get_cached_model_provider") as mock_create_provider,
        patch("src.sdk.runner.get_native_tools", return_value=[]),
        patch("src.sdk.runner._seed_default_workspace"),
        patch("src.sdk.runner._get_system_prompt", return_value="You are test assistant."),
        patch("src.storage.messages.get_message_store") as mock_get_store,
    ):
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = True
        settings.memory.summarization.get_trigger = lambda: ("tokens", 10)
        settings.memory.summarization.get_keep = lambda: ("messages", 5)
        settings.memory.summarization.model = "ollama:test-model"
        settings.memory.summarization.trim_tokens_to_summarize = 4000
        settings.memory.summarization.trigger_tokens = None
        settings.memory.summarization.keep_tokens = None
        settings.agent.model = "ollama:test-model"

        mock_create_provider.return_value = MockStreamProvider()

        mock_store = MagicMock()
        mock_store.add_summary_message.return_value = "summary-stream"
        mock_get_store.return_value = mock_store

        # Pre-create the loop so we can mock _acreate_summary on the middleware
        loop = await get_sdk_loop(
            user_id="test_stream_user",
            workspace_id="personal",
            session_id="stream-chat",
        )

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
        from src.sdk.run_models import UsageAggregate

        summarization_mw._acreate_summary = AsyncMock(
            return_value=(summary_text, UsageAggregate())
        )

        long_msgs = [
            Message(role="user", content=f"Message number {i} about various topics.", storage_id=f"m{i}")
            for i in range(30)
        ]

        chunks = []
        async for chunk in run_sdk_agent_stream(
            user_id="test_stream_user",
            messages=long_msgs,
            workspace_id="personal",
            session_id="stream-chat",
        ):
            chunks.append(chunk)

        assert mock_store.add_summary_message.called, (
            "add_summary_message should have been called when summarization triggered"
        )
        assert mock_store.add_summary_message.call_args.kwargs["session_id"] == "stream-chat"


class _ItemScopesImported(BaseException):
    pass


@pytest.mark.asyncio
async def test_create_sdk_loop_does_not_import_item_scopes(monkeypatch):
    """Runtime loop construction must not depend on workspace item scopes."""
    from src.sdk.runner import create_sdk_loop
    from src.sdk.tools import ToolDefinition

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "src.sdk.item_scopes":
            raise _ItemScopesImported(name)
        return real_import(name, *args, **kwargs)

    class FakeIndex:
        def count(self):
            return 1

        def index_tool(self, *args, **kwargs):
            pass

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with (
        patch("src.sdk.runner.get_settings") as mock_settings,
        patch("src.sdk.runner.get_cached_model_provider") as mock_create_provider,
        patch(
            "src.sdk.runner.get_native_tools",
            return_value=[
                ToolDefinition(name="demo_tool", description="Demo", parameters={}, function=lambda: "ok")
            ],
        ),
        patch("src.sdk.runner._seed_default_workspace"),
        patch("src.sdk.runner._get_system_prompt", return_value="You are test assistant."),
        patch("src.sdk.tool_index.get_or_create_index", return_value=FakeIndex()),
    ):
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = False
        settings.agent.model = "ollama:test-model"
        mock_provider = AsyncMock()
        mock_provider.provider_id = "ollama"
        mock_provider.model = "ollama:test-model"
        mock_create_provider.return_value = mock_provider

        loop = await create_sdk_loop(user_id="test_user", workspace_id="disabled_workspace")

    assert loop is not None


def test_skills_context_ignores_item_scopes(monkeypatch, tmp_path):
    """Prompt skill catalog is user-level and must not import item scopes."""
    from src.sdk import runner

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "src.sdk.item_scopes":
            raise _ItemScopesImported(name)
        return real_import(name, *args, **kwargs)

    class FakeRegistry:
        def get_all_skills(self):
            return [{"name": "helper", "description": "Helps", "metadata": {}}]

        def get_load_count(self, name: str) -> int:
            return 0

    class FakePaths:
        base = tmp_path

        def user_skills_dir(self):
            return tmp_path / "skills"

        def user_subagents_dir(self):
            return tmp_path / "subagents"

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    monkeypatch.setattr("src.skills.registry.get_skill_registry", lambda **kwargs: FakeRegistry())
    monkeypatch.setattr("src.storage.paths.get_paths", lambda *args, **kwargs: FakePaths())

    context = runner._get_skills_context("test_user", "disabled_workspace")

    assert "helper" in context


def test_reset_sdk_loop_with_session_removes_all_model_and_key_variants():
    from src.sdk import runner

    runner._loop_cache.clear()
    runner._loop_cache[runner._loop_cache_key("u", "personal", None, None, "chat-1")] = "default"
    runner._loop_cache[runner._loop_cache_key("u", "workspace-a", "model-a", None, "chat-1")] = "model"
    runner._loop_cache[
        runner._loop_cache_key("u", "workspace-b", "model-a", {"openai": "key"}, "chat-1")
    ] = "keys"
    runner._loop_cache[runner._loop_cache_key("u", "personal", "model-a", None, "chat-2")] = "other"
    runner._loop_cache[runner._loop_cache_key("other", "personal", "model-a", None, "chat-1")] = "other-user"

    removed = runner.reset_sdk_loop("u", workspace_id="ignored-workspace", session_id="chat-1")

    assert removed == 3
    assert list(runner._loop_cache.values()) == ["other", "other-user"]


def test_reset_sdk_loop_with_session_matches_exact_session_segment():
    from src.sdk import runner

    runner._loop_cache.clear()
    runner._loop_cache[runner._loop_cache_key("u", "personal", "model-a", None, "chat")] = "chat"
    runner._loop_cache[runner._loop_cache_key("u", "personal", "model-a", None, "chat-1")] = "chat-1"
    runner._loop_cache[
        runner._loop_cache_key("u", "personal", "model-a", {"openai": "key"}, "chat")
    ] = "chat-keys"

    removed = runner.reset_sdk_loop("u", session_id="chat")

    assert removed == 2
    assert list(runner._loop_cache.values()) == ["chat-1"]


def test_loop_cache_key_ignores_workspace_but_keeps_session_model_and_provider_keys():
    from src.sdk import runner

    personal_key = runner._loop_cache_key(
        "u", "personal", "model-a", {"openai": "key"}, "chat-1"
    )
    workspace_key = runner._loop_cache_key(
        "u", "other-workspace", "model-a", {"openai": "key"}, "chat-1"
    )

    assert workspace_key == personal_key
    assert runner._loop_cache_key("u", "personal", None, None, "chat-1") != personal_key
    assert runner._loop_cache_key("u", "personal", "model-a", None, "chat-1") != personal_key
    assert runner._loop_cache_key("u", "personal", "model-a", {"openai": "other"}, "chat-1") != personal_key
    assert runner._loop_cache_key("u", "personal", "model-a", {"openai": "key"}, "chat-2") != personal_key


def test_reset_sdk_loop_without_session_removes_all_user_sessions():
    from src.sdk import runner

    runner._loop_cache.clear()
    runner._loop_cache[runner._loop_cache_key("u", "personal", None, None, "default")] = "default"
    runner._loop_cache[runner._loop_cache_key("u", "workspace-a", "model-a", None, "chat-1")] = "chat-1"
    runner._loop_cache[runner._loop_cache_key("u", "workspace-b", "model-b", None, "chat-2")] = "chat-2"
    runner._loop_cache[runner._loop_cache_key("other", "personal", None, None, "default")] = "other-user"

    removed = runner.reset_sdk_loop("u", workspace_id="ignored-workspace")

    assert removed == 3
    assert list(runner._loop_cache.values()) == ["other-user"]


def test_active_loop_registry_is_session_aware():
    from src.sdk import runner

    loop_1 = object()
    loop_2 = object()
    runner._user_loops.clear()

    runner.register_user_loop("u", loop_1, session_id="chat-1")
    runner.register_user_loop("u", loop_2, session_id="chat-2")

    assert runner.get_user_loop("u", session_id="chat-1") is loop_1
    assert runner.get_user_loop("u", session_id="chat-2") is loop_2

    runner.unregister_user_loop("u", session_id="chat-1")

    assert runner.get_user_loop("u", session_id="chat-1") is None
    assert runner.get_user_loop("u", session_id="chat-2") is loop_2


def test_unregister_user_loop_does_not_remove_newer_registered_loop():
    from src.sdk import runner

    old_loop = object()
    new_loop = object()
    runner._user_loops.clear()

    runner.register_user_loop("u", old_loop, session_id="chat-1")
    runner.register_user_loop("u", new_loop, session_id="chat-1")
    runner.unregister_user_loop("u", old_loop, session_id="chat-1")

    assert runner.get_user_loop("u", session_id="chat-1") is new_loop


def test_default_active_loop_does_not_clobber_named_sessions():
    from src.sdk import runner

    default_loop = object()
    session_loop = object()
    runner._user_loops.clear()

    runner.register_user_loop("u", default_loop, session_id="default")
    runner.register_user_loop("u", session_loop, session_id="chat-1")

    assert runner.get_user_loop("u", session_id="default") is default_loop
    assert runner.get_user_loop("u", session_id="chat-1") is session_loop

    runner.unregister_user_loop("u", session_id="default")

    assert runner.get_user_loop("u", session_id="default") is None
    assert runner.get_user_loop("u", session_id="chat-1") is session_loop

    runner.unregister_user_loop("u", session_id="chat-1")

    assert runner.get_user_loop("u", session_id="chat-1") is None


def test_whitespace_session_id_uses_default_identity():
    from src.sdk import runner

    loop = object()
    runner._user_loops.clear()

    runner.register_user_loop("u", loop, session_id="   ")

    assert runner.get_user_loop("u", session_id="default") is loop
    assert runner._loop_cache_key("u", "personal", None, session_id="   ").endswith(
        ":session:default"
    )

    runner._loop_cache["u:model:default:session:default"] = object()
    assert runner.reset_sdk_loop("u", session_id="   ") == 1


def test_get_user_loop_without_session_does_not_choose_among_multiple_sessions():
    from src.sdk import runner

    runner._user_loops.clear()

    runner.register_user_loop("u", object(), session_id="chat-1")
    runner.register_user_loop("u", object(), session_id="chat-2")

    assert runner.get_user_loop("u") is None


def test_skills_context_excludes_user_disabled_skills(monkeypatch, tmp_path):
    from src.sdk import runner

    class FakeRegistry:
        def get_all_skills(self):
            return [
                {"name": "enabled-helper", "description": "Enabled", "metadata": {}},
                {"name": "disabled-helper", "description": "Disabled", "metadata": {}},
            ]

        def get_load_count(self, name: str) -> int:
            return 0

    class FakePaths:
        @property
        def root(self):
            return tmp_path

        def user_skills_dir(self):
            return tmp_path / "skills"

        def user_subagents_dir(self):
            return tmp_path / "subagents"

    (tmp_path / "capabilities.yaml").write_text(
        "skills:\n  disabled-helper: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("src.sdk.capabilities.user_capabilities_root", lambda user_id: tmp_path)
    monkeypatch.setattr("src.skills.registry.get_skill_registry", lambda **kwargs: FakeRegistry())
    monkeypatch.setattr("src.storage.paths.get_paths", lambda *args, **kwargs: FakePaths())

    context = runner._get_skills_context("test_user", "ignored-workspace")

    assert "enabled-helper" in context
    assert "disabled-helper" not in context


@pytest.mark.asyncio
async def test_create_sdk_loop_excludes_disabled_tools_from_core_and_index(monkeypatch, tmp_path):
    from src.sdk.runner import create_sdk_loop
    from src.sdk.tools import ToolDefinition

    class FakePaths:
        @property
        def root(self):
            return tmp_path

        def user_tools_dir(self):
            return tmp_path / "Tools"

        def workspace_tools_dir(self):
            return tmp_path / "Workspaces" / "ignored" / "Tools"

        def user_mcp_config(self):
            return tmp_path / ".mcp.json"

    class FakeIndex:
        def __init__(self):
            self.indexed = []

        def count(self):
            return 0

        def index_tool(self, td, *args, **kwargs):
            self.indexed.append(td.name)

    fake_index = FakeIndex()
    (tmp_path / "capabilities.yaml").write_text(
        "tools:\n  time_get: false\n  shell_execute: false\n  custom_disabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("src.sdk.capabilities.user_capabilities_root", lambda user_id: tmp_path)

    with (
        patch("src.sdk.runner.get_settings") as mock_settings,
        patch("src.sdk.runner.get_cached_model_provider") as mock_create_provider,
        patch(
            "src.sdk.runner.get_native_tools",
            return_value=[
                ToolDefinition(name="time_get", description="Time", parameters={}, function=lambda: "ok"),
                ToolDefinition(name="shell_execute", description="Shell", parameters={}, function=lambda: "ok"),
            ],
        ),
        patch("src.sdk.runner._seed_default_workspace"),
        patch("src.sdk.runner._get_system_prompt", return_value="You are test assistant."),
        patch("src.storage.paths.get_paths", return_value=FakePaths()),
        patch("src.sdk.tool_index.get_or_create_index", return_value=fake_index),
        patch(
            "src.sdk.tools_custom.scan_tools_dir",
            return_value=[ToolDefinition(name="custom_disabled", description="Custom", parameters={})],
        ),
    ):
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = False
        settings.agent.model = "ollama:test-model"
        mock_provider = AsyncMock()
        mock_provider.provider_id = "ollama"
        mock_provider.model = "ollama:test-model"
        mock_create_provider.return_value = mock_provider

        loop = await create_sdk_loop(user_id="test_user", workspace_id="ignored-workspace")

    registered_names = set(loop._registry.list_names())
    assert "shell_execute" not in registered_names
    assert "time_get" not in fake_index.indexed
    assert "custom_disabled" not in fake_index.indexed


@pytest.mark.asyncio
async def test_create_sdk_loop_rebuilds_tool_index_when_capabilities_change(monkeypatch, tmp_path):
    from src.sdk.runner import create_sdk_loop
    from src.sdk.tools import ToolDefinition

    class FakePaths:
        @property
        def root(self):
            return tmp_path

        def user_tools_dir(self):
            return tmp_path / "Tools"

        def workspace_tools_dir(self):
            return tmp_path / "Workspaces" / "ignored" / "Tools"

        def user_mcp_config(self):
            return tmp_path / ".mcp.json"

    class FakeIndex:
        def __init__(self):
            self.indexed = []
            self.cleared = False

        def count(self):
            # Simulates a persisted index that already has data — the runner
            # should NOT clear it (get_or_create_index handles clearing when
            # source hashes change). With count() > 0 the re-indexing loop
            # is skipped, which is the fast path after the fix.
            return 1

        def clear(self):
            self.cleared = True

        def index_tool(self, td, *args, **kwargs):
            self.indexed.append(td.name)

    fake_index = FakeIndex()
    monkeypatch.setattr("src.sdk.capabilities.user_capabilities_root", lambda user_id: tmp_path)

    with (
        patch("src.sdk.runner.get_settings") as mock_settings,
        patch("src.sdk.runner.get_cached_model_provider") as mock_create_provider,
        patch(
            "src.sdk.runner.get_native_tools",
            return_value=[
                ToolDefinition(name="demo_lookup", description="Lookup", parameters={}, function=lambda: "ok"),
            ],
        ),
        patch("src.sdk.runner._seed_default_workspace"),
        patch("src.sdk.runner._get_system_prompt", return_value="You are test assistant."),
        patch("src.storage.paths.get_paths", return_value=FakePaths()),
        patch("src.sdk.tool_index.get_or_create_index", return_value=fake_index),
    ):
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = False
        settings.agent.model = "ollama:test-model"
        mock_provider = AsyncMock()
        mock_provider.provider_id = "ollama"
        mock_provider.model = "ollama:test-model"
        mock_create_provider.return_value = mock_provider

        await create_sdk_loop(user_id="test_user", workspace_id="ignored-workspace")

    # The runner must NOT clear a persisted index — that would force a ~23s
    # chromadb re-embedding of all tools on every new session. Clearing is
    # the responsibility of get_or_create_index when source hashes change.
    assert fake_index.cleared is False
    # Since the index already had data (count() > 0), no re-indexing occurs.
    assert fake_index.indexed == []


def test_tool_reload_filters_disabled_mcp_and_connector_tools(monkeypatch, tmp_path):
    from src.sdk.loop import AgentLoop, _current_agent_loop
    from src.sdk.tool_index import ToolIndex
    from src.sdk.tools import ToolDefinition
    from src.sdk.tools_core.tool_reload import tool_reload

    class FakeProvider:
        provider_id = "fake"

        async def chat(self, messages, tools=None, model=None, **kwargs):
            return Message.assistant("")

        def get_model_info(self, model=None):
            from src.sdk.providers.base import ModelInfo

            return ModelInfo(id=model or "fake", provider_id="fake")

        def count_tokens(self, messages):
            return 0

    class FakeMCPBridge:
        def get_tool_definitions(self):
            return [ToolDefinition(name="mcp__server__disabled", description="Disabled MCP")]

    class FakeConnectorBridge:
        def get_tool_definitions(self):
            return [
                {
                    "name": "connector__disabled",
                    "description": "Disabled connector",
                    "annotations": {"read_only": True, "destructive": False},
                    "function": lambda **kwargs: "ok",
                }
            ]

    class FakePaths:
        def user_tools_dir(self):
            path = tmp_path / "Tools"
            path.mkdir(parents=True, exist_ok=True)
            return path

        def workspace_tools_dir(self):
            path = tmp_path / "WorkspaceTools"
            path.mkdir(parents=True, exist_ok=True)
            return path

        def user_mcp_config(self):
            return tmp_path / ".mcp.json"

    (tmp_path / "capabilities.yaml").write_text(
        "tools:\n  mcp__server__disabled: false\n  connector__disabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("src.sdk.capabilities.user_capabilities_root", lambda user_id: tmp_path)
    monkeypatch.setattr("src.storage.paths.get_paths", lambda **kwargs: FakePaths())
    monkeypatch.setattr("src.sdk.tools_custom.get_custom_tools", lambda **kwargs: [])

    idx = ToolIndex(tmp_path / "idx")
    loop = AgentLoop(provider=FakeProvider(), tools=[])
    loop.user_id = "test_user"
    loop.workspace_id = "personal"
    loop._tool_index = idx
    loop._mcp_bridge = FakeMCPBridge()
    loop._connectkit_bridge = FakeConnectorBridge()
    token = _current_agent_loop.set(loop)
    try:
        result = tool_reload.invoke({})
        names = idx.list_all_names()
    finally:
        _current_agent_loop.reset(token)
        idx.close()

    assert "0 MCP" in result
    assert "0 connector" in result
    assert "mcp__server__disabled" not in names
    assert "connector__disabled" not in names


@pytest.mark.asyncio
async def test_runner_wraps_loop_with_langfuse(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

    import src.config.settings as _cfg
    _cfg._config = None

    from src.sdk.langfuse_tracer import LangfuseTracer
    LangfuseTracer._client = None

    wrap_calls = []
    original_wrap = LangfuseTracer.wrap_loop

    def tracking_wrap(loop, user_id, session_id):
        wrap_calls.append({"user_id": user_id, "session_id": session_id})
        return original_wrap(loop, user_id, session_id)

    monkeypatch.setattr(LangfuseTracer, "wrap_loop", tracking_wrap)

    from src.sdk.runner import create_sdk_loop
    await create_sdk_loop(
        "lf_test_user", model="ollama-cloud:test-model", session_id="lf-chat"
    )

    assert LangfuseTracer.is_enabled() is True
    assert len(wrap_calls) == 1
    assert wrap_calls[0]["user_id"] == "lf_test_user"
    assert wrap_calls[0]["session_id"] == "lf-chat"

    LangfuseTracer._client = None
    _cfg._config = None


@pytest.mark.asyncio
async def test_create_sdk_loop_uses_saved_summarization_model(monkeypatch):
    """The user's saved summarization_model wins over host config."""
    from src.config.user_settings import SavedUserSettings
    from src.sdk import runner

    settings = MagicMock()
    settings.memory.summarization.enabled = True
    settings.memory.summarization.get_trigger.return_value = ("messages", 2)
    settings.memory.summarization.get_keep.return_value = ("messages", 1)
    settings.memory.summarization.model = "openai:host-summary"
    settings.memory.summarization.trim_tokens_to_summarize = 4000
    settings.memory.summarization.prompt_file = None
    settings.verification.enabled = False
    settings.langfuse.enabled = False
    monkeypatch.setattr(runner, "get_settings", lambda: settings)
    monkeypatch.setattr(runner, "get_native_tools", lambda: [])
    monkeypatch.setattr(runner, "_seed_default_workspace", lambda: None)
    monkeypatch.setattr(runner, "_get_system_prompt", lambda *args, **kwargs: "prompt")
    monkeypatch.setattr(
        "src.config.user_settings_service.load_saved_user_settings",
        lambda user_id: SavedUserSettings(summarization_model="anthropic:saved-summary"),
    )
    monkeypatch.setattr("src.sdk.tool_index.get_or_create_index", lambda *args, **kwargs: _FakeIndex())

    provider = AsyncMock()
    provider.provider_id = "openai"
    provider.model = "gpt-4.1"
    monkeypatch.setattr(runner, "get_cached_model_provider", lambda *args, **kwargs: provider)

    captured = {}

    def fake_summarization_mw(**kwargs):
        captured["model"] = kwargs.get("model")
        return MagicMock()

    monkeypatch.setattr(runner, "SummarizationMiddleware", fake_summarization_mw)

    await runner.create_sdk_loop(user_id="test_user", session_id="s1")

    assert captured["model"] == "anthropic:saved-summary"
