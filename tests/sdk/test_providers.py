"""Tests for Phase 2 LLM Providers.

Tests use mocked HTTP responses to verify:
- Provider instantiation and config
- Request format for each provider's API
- Response parsing into SDK Message/ToolCall types
- Tool call extraction
- Factory config resolution
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config.user_settings import SavedUserSettings
from src.config.user_settings_store import UserSettingsStore
from src.sdk.messages import Message
from src.sdk.providers.anthropic import AnthropicProvider
from src.sdk.providers.factory import (
    _ENV_KEY_MAP,
    _PROVIDER_CLASSES,
    _default_base_url,
    _parse_model_string,
    create_model_from_config,
    create_provider,
    create_provider_from_registry_model,
)
from src.sdk.providers.gemini import GeminiProvider
from src.sdk.providers.ollama import OllamaCloud
from src.sdk.providers.openai import OpenAIProvider
from src.sdk.tools import tool
from src.storage.paths import DataPaths


def _host_settings(model: str = "ollama:host-model") -> SimpleNamespace:
    return SimpleNamespace(
        agent=SimpleNamespace(model=model),
        langfuse=SimpleNamespace(enabled=False, public_key="", secret_key="", host=""),
    )


def _create_with_saved_settings(
    saved: SavedUserSettings,
    *,
    config_model: str | None = None,
    provider_keys: dict[str, str] | None = None,
) -> tuple[MagicMock, MagicMock]:
    store = MagicMock()
    store.load.return_value = saved
    provider = MagicMock()
    with (
        patch("src.config.get_settings", return_value=_host_settings()),
        patch("src.sdk.providers.factory._user_settings_store.UserSettingsStore", return_value=store),
        patch("src.sdk.providers.factory.create_provider_from_registry_model", return_value=None),
        patch("src.sdk.providers.factory.create_provider", return_value=provider) as create,
    ):
        result = create_model_from_config(
            config_model, provider_keys=provider_keys, user_id="alice"
        )
    assert result is provider
    return store, create


def _user_settings_store(tmp_path: Path, user_id: str = "alice") -> UserSettingsStore:
    paths = DataPaths(
        deployment="solo",
        data_path=str(tmp_path / "project"),
        ea_root=str(tmp_path / "home"),
        user_id=user_id,
    )
    return UserSettingsStore(user_id, paths=paths)


def test_context_overflow_mapper_raises_for_413_status():
    from types import SimpleNamespace

    from src.sdk.providers.base import ProviderContextOverflowError, raise_if_context_overflow

    exc = RuntimeError("request failed")
    exc.response = SimpleNamespace(status_code=413, text="payload too large")

    with pytest.raises(ProviderContextOverflowError):
        raise_if_context_overflow(exc)


def test_context_overflow_mapper_raises_for_context_length_text():
    from src.sdk.providers.base import ProviderContextOverflowError, raise_if_context_overflow

    with pytest.raises(ProviderContextOverflowError):
        raise_if_context_overflow(RuntimeError("maximum context length exceeded"))

# ─── Fixtures ───


@tool
def time_get(user_id: str = "default_user") -> str:
    """Get the current time."""
    import datetime

    return datetime.datetime.now().isoformat()


@pytest.fixture
def tool_defs():
    return [time_get]


# ─── Base Interface Tests ───


class TestLLMProviderInterface:
    def test_provider_classes_has_all_types(self):
        expected = {"openai", "openai-compatible", "anthropic", "gemini"}
        assert set(_PROVIDER_CLASSES.keys()) == expected

    def test_env_key_map_covers_major_providers(self):
        required = {"openai", "anthropic", "gemini", "ollama-cloud"}
        assert required.issubset(set(_ENV_KEY_MAP.keys()))

    def test_default_url_returns_valid_urls(self):
        for provider in ["groq", "deepseek", "together", "openrouter"]:
            url = _default_base_url(provider)
            assert url.startswith("http"), f"{provider} URL must start with http"


# ─── Ollama Provider Tests ───


class TestLocalCompatibleProvider:
    def test_default_config(self):
        """MERGED: OllamaLocal merged into OpenAIProvider. Test need openai SDK mock pattern."""
        p = OpenAIProvider(base_url="http://localhost:11434/v1")
        assert p.provider_id == "openai"

    def test_custom_base_url(self):
        p = OpenAIProvider(base_url="http://myserver:11434/v1")
        assert p.provider_id == "openai"

    def test_get_model_info_returns_defaults(self):
        p = OpenAIProvider()
        info = p.get_model_info("foo")
        assert info.provider_id == "openai"
        assert info.id == "foo"


class TestOllamaCloudProvider:
    def test_default_config(self):
        p = OllamaCloud(api_key="test-key")
        assert p.provider_id == "ollama-cloud"
        assert p.base_url == "https://ollama.com"
        assert p.model == "minimax-m2.5"
        assert p.api_key == "test-key"

    def test_native_payload_format(self):
        p = OllamaCloud(api_key="test-key")
        msgs = [Message.system("You are helpful."), Message.user("Hello")]
        payload = p._build_payload(msgs, None, "minimax-m2.5")
        assert payload["model"] == "minimax-m2.5"
        assert len(payload["messages"]) == 2
        assert payload["stream"] is False

    def test_native_payload_with_tools(self, tool_defs):
        p = OllamaCloud(api_key="test-key")
        msgs = [Message.user("What time is it?")]
        payload = p._build_payload(msgs, tool_defs, "minimax-m2.5")
        assert "tools" in payload
        assert payload["tools"][0]["type"] == "function"
        assert payload["tools"][0]["function"]["name"] == "time_get"

    def test_parse_native_response_with_tool_calls(self):
        p = OllamaCloud(api_key="test-key")
        data = {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "time_get", "arguments": {"user_id": "test"}},
                    }
                ],
            },
            "done": True,
        }
        msg = p._parse_response(data)
        assert msg.role == "assistant"
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "time_get"

    def test_parse_native_response_text_only(self):
        p = OllamaCloud(api_key="test-key")
        data = {"message": {"role": "assistant", "content": "Hello!"}, "done": True}
        msg = p._parse_response(data)
        assert msg.role == "assistant"
        assert msg.content == "Hello!"

    def test_native_chunk_done(self):
        p = OllamaCloud(api_key="test-key")
        chunks = p._parse_chunk({"message": {"content": "Done"}, "done": True}, {})
        assert any(c.type == "done" for c in chunks)

    def test_native_chunk_done_with_content_emits_text_delta_first(self):
        p = OllamaCloud(api_key="test-key")
        chunks = p._parse_chunk({"message": {"content": "Done"}, "done": True}, {})

        text = next(c for c in chunks if c.canonical_type == "text_delta")
        done = next(c for c in chunks if c.type == "done")
        assert text.content == "Done"
        assert chunks.index(text) < chunks.index(done)

    def test_native_chunk_token(self):
        p = OllamaCloud(api_key="test-key")
        chunks = p._parse_chunk({"message": {"content": "Hi"}, "done": False}, {})
        assert any(c.canonical_type == "text_delta" for c in chunks)
        token = next(c for c in chunks if c.canonical_type == "text_delta")
        assert token.content == "Hi"

    def test_parse_native_response_estimates_reasoning_tokens(self):
        """Ollama's native API doesn't report reasoning tokens separately;
        the provider estimates them from the thinking content."""
        p = OllamaCloud(api_key="test-key")
        thinking = "Let me think about this carefully before answering."
        data = {
            "message": {"role": "assistant", "content": "Answer", "thinking": thinking},
            "done": True,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        msg = p._parse_response(data)
        assert msg.usage is not None
        assert msg.usage.reasoning_tokens == len(thinking) // 4
        assert msg.usage.output_tokens == 5

    def test_parse_native_response_no_thinking_has_zero_reasoning(self):
        p = OllamaCloud(api_key="test-key")
        data = {
            "message": {"role": "assistant", "content": "Answer"},
            "done": True,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        msg = p._parse_response(data)
        assert msg.usage is not None
        assert msg.usage.reasoning_tokens == 0

    def test_chat_stream_usage_estimates_reasoning_tokens(self, monkeypatch):
        """Streaming: reasoning deltas accumulate across chunks and the final
        usage event carries the estimate."""
        import asyncio
        import json

        p = OllamaCloud(api_key="test-key")
        lines = [
            json.dumps({"message": {"content": "", "thinking": "Let me think"}, "done": False}),
            json.dumps({"message": {"content": "Answer"}, "done": True, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}),
        ]

        class FakeStream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                for line in lines:
                    yield line

        class FakeClient:
            def stream(self, method, url, json=None):
                return FakeStream()

        monkeypatch.setattr(p, "_get_client", lambda: FakeClient())

        chunks: list = []

        async def collect():
            async for c in p.chat_stream([Message.user("hi")]):
                chunks.append(c)

        asyncio.run(collect())
        usage = next(c for c in chunks if c.type == "usage")
        assert usage.usage.reasoning_tokens == len("Let me think") // 4
        assert usage.usage.output_tokens == 5

    def test_count_tokens_returns_positive(self):
        p = OllamaCloud(api_key="test-key")
        assert p.count_tokens("hello world") > 0

    def test_get_model_info_returns_defaults(self):
        p = OllamaCloud(api_key="test-key")
        info = p.get_model_info("minimax-m2.5")
        assert info.provider_id == "ollama-cloud"
        assert info.id == "minimax-m2.5"


# ─── OpenAI Provider Tests ───




    def test_native_payload_maps_max_tokens_to_num_predict(self):
        """OpenAI-style max_tokens must map to Ollama's options.num_predict."""
        p = OllamaCloud(api_key="test-key")
        msgs = [Message.user("hi")]
        payload = p._build_payload(
            msgs, None, "minimax-m2.5",
            provider_options={"ollama-cloud": {"max_tokens": 800}},
        )
        assert payload["options"]["num_predict"] == 800
        assert "max_tokens" not in payload

    def test_native_payload_does_not_mutate_shared_provider_options(self):
        """_build_payload must not mutate the caller's provider_options dict
        (the grader loop shares its run_config.provider_options across calls)."""
        p = OllamaCloud(api_key="test-key")
        shared = {"ollama-cloud": {"max_tokens": 800}}
        p._build_payload([], None, "m", provider_options=shared)
        assert shared == {"ollama-cloud": {"max_tokens": 800}}


    def test_native_payload_maps_kwarg_max_tokens_to_num_predict(self):
        """max_tokens passed as a direct kwarg must also map to num_predict
        (Ollama's native API silently ignores a top-level max_tokens)."""
        p = OllamaCloud(api_key="test-key")
        payload = p._build_payload([], None, "m", max_tokens=20)
        assert payload["options"]["num_predict"] == 20
        assert "max_tokens" not in payload


    def test_is_timeout_error_classifies_transient_errors(self):
        import httpx

        from src.sdk.providers.base import is_timeout_error
        assert is_timeout_error(httpx.ReadTimeout("stall"))
        assert is_timeout_error(httpx.ConnectTimeout("no route"))
        assert is_timeout_error(httpx.ConnectError("refused"))
        assert not is_timeout_error(ValueError("bad"))
        assert not is_timeout_error(RuntimeError("boom"))

    @pytest.mark.asyncio
    async def test_chat_retries_once_on_timeout(self, monkeypatch):
        import httpx

        p = OllamaCloud(api_key="test-key")
        calls = {"n": 0}

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"role": "assistant", "content": "Hello"}, "done": True}

        class FakeClient:
            async def post(self, url, json=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise httpx.ReadTimeout("stall")
                return FakeResp()

        monkeypatch.setattr(p, "_get_client", lambda: FakeClient())
        msg = await p.chat([Message.user("hi")])
        assert msg.content == "Hello"
        assert calls["n"] == 2  # exactly one retry

    @pytest.mark.asyncio
    async def test_chat_retries_at_most_once(self, monkeypatch):
        import httpx

        p = OllamaCloud(api_key="test-key")
        calls = {"n": 0}

        class FakeClient:
            async def post(self, url, json=None):
                calls["n"] += 1
                raise httpx.ReadTimeout("stall")

        monkeypatch.setattr(p, "_get_client", lambda: FakeClient())
        with pytest.raises(httpx.ReadTimeout):
            await p.chat([Message.user("hi")])
        assert calls["n"] == 2  # first + one retry, then give up

    @pytest.mark.asyncio
    async def test_chat_stream_retries_before_first_token(self, monkeypatch):
        import json

        import httpx

        p = OllamaCloud(api_key="test-key")
        attempts = {"n": 0}
        lines = [json.dumps({"message": {"content": "Hi"}, "done": True, "usage": {"prompt_tokens": 1, "completion_tokens": 1}})]

        class FakeStream:
            def __init__(self):
                attempts["n"] += 1

            async def __aenter__(self):
                if attempts["n"] == 1:
                    raise httpx.ReadTimeout("stall")
                return self

            async def __aexit__(self, *a):
                return False

            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                for line in lines:
                    yield line

        class FakeClient:
            def stream(self, method, url, json=None):
                return FakeStream()

        monkeypatch.setattr(p, "_get_client", lambda: FakeClient())

        chunks = []

        async def collect():
            async for c in p.chat_stream([Message.user("hi")]):
                chunks.append(c)

        await collect()
        assert attempts["n"] == 2  # timed out before any content → retried
        assert any(c.canonical_type == "text_delta" for c in chunks)

    @pytest.mark.asyncio
    async def test_chat_stream_does_not_retry_after_content(self, monkeypatch):
        import json

        import httpx

        p = OllamaCloud(api_key="test-key")
        attempts = {"n": 0}

        class FakeStream:
            def __init__(self):
                attempts["n"] += 1

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                yield json.dumps({"message": {"content": "partial"}, "done": False})
                raise httpx.ReadTimeout("stall")

        class FakeClient:
            def stream(self, method, url, json=None):
                return FakeStream()

        monkeypatch.setattr(p, "_get_client", lambda: FakeClient())

        got = []

        async def collect():
            async for c in p.chat_stream([Message.user("hi")]):
                got.append(c)

        with pytest.raises(httpx.ReadTimeout):
            await collect()
        assert attempts["n"] == 1  # content was already emitted → no retry
        assert any(c.canonical_type == "text_delta" for c in got)


class TestOpenAIProvider:
    def test_default_config(self):
        p = OpenAIProvider(api_key="sk-test")
        assert p.provider_id == "openai"
        assert p.model == "gpt-4o"

    def test_custom_base_url(self):
        p = OpenAIProvider(api_key="sk-test", base_url="https://api.groq.com/openai/v1")
        assert p.provider_id == "openai"

    def test_custom_provider_id_and_default_options(self):
        p = OpenAIProvider(
            api_key="sk-test",
            base_url="https://apihub.agnes-ai.com/v1",
            model="agnes-2.0-flash",
            provider_id="agnes",
            default_provider_options={"chat_template_kwargs": {"enable_thinking": True}},
        )

        assert p.provider_id == "agnes"
        assert p._extract_provider_options(None) == {
            "chat_template_kwargs": {"enable_thinking": True}
        }
        assert p._extract_provider_options(
            {"agnes": {"temperature": 0}, "openai": {"temperature": 1}}
        ) == {
            "chat_template_kwargs": {"enable_thinking": True},
            "temperature": 0,
        }

    def test_get_model_info_preserves_openai_identity(self):
        provider = OpenAIProvider(model="gpt-4.1")

        assert provider.get_model_info("gpt-4.1").provider_id == "openai"

    def test_get_model_info_preserves_local_ollama_identity(self):
        provider = create_provider("ollama", model="qwen3:8b")

        assert provider.get_model_info("qwen3:8b").provider_id == "ollama"

    def test_get_model_info_preserves_custom_compatible_identity(self):
        provider = create_provider(
            "groq",
            model="llama-3.1-70b-versatile",
            api_key="gsk-test",
        )

        assert provider.get_model_info("llama-3.1-70b-versatile").provider_id == "groq"

    def test_extension_fields_move_to_extra_body(self):
        p = OpenAIProvider(api_key="sk-test")
        params = {
            "model": "agnes-2.0-flash",
            "chat_template_kwargs": {"enable_thinking": True},
            "thinking": {"type": "enabled", "budget_tokens": 2048},
        }

        p._move_extension_fields_to_extra_body(params)

        assert "chat_template_kwargs" not in params
        assert "thinking" not in params
        assert params["extra_body"] == {
            "chat_template_kwargs": {"enable_thinking": True},
            "thinking": {"type": "enabled", "budget_tokens": 2048},
        }

    def test_parse_response_text_only(self):
        p = OpenAIProvider(api_key="sk-test")
        from types import SimpleNamespace

        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hello!", tool_calls=None))]
        )
        msg = p._parse_response(response)
        assert msg.role == "assistant"
        assert msg.content == "Hello!"

    def test_parse_response_with_tool_calls(self):
        p = OpenAIProvider(api_key="sk-test")
        from types import SimpleNamespace

        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="call_abc",
                                function=SimpleNamespace(
                                    name="time_get",
                                    arguments='{"user_id": "test"}',
                                ),
                            )
                        ],
                    )
                )
            ]
        )
        msg = p._parse_response(response)
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "time_get"
        assert msg.tool_calls[0].arguments == {"user_id": "test"}


# ─── Anthropic Provider Tests ───


class TestAnthropicProvider:
    def test_default_config(self):
        p = AnthropicProvider(api_key="sk-ant-test")
        assert p.provider_id == "anthropic"
        assert p.model == "claude-sonnet-4-20250514"

    def test_build_payload_with_system(self):
        p = AnthropicProvider(api_key="sk-ant-test")
        msgs = [Message.system("Be helpful"), Message.user("Hi")]
        payload = p._build_payload(msgs, None, "claude-sonnet-4-20250514")
        assert "system" in payload
        assert payload["system"] == "Be helpful"
        assert len(payload["messages"]) == 1

    def test_build_payload_extracts_system(self):
        p = AnthropicProvider(api_key="sk-ant-test")
        msgs = [
            Message.system("System prompt"),
            Message.user("First"),
            Message.assistant("Reply"),
            Message.user("Second"),
        ]
        payload = p._build_payload(msgs, None, "claude-sonnet-4-20250514")
        assert payload["system"] == "System prompt"
        assert len(payload["messages"]) == 3

    def test_parse_response_text(self):
        p = AnthropicProvider(api_key="sk-ant-test")
        data = {
            "content": [{"type": "text", "text": "Hello!"}],
            "stop_reason": "end_turn",
        }
        msg = p._parse_response(data)
        assert msg.content == "Hello!"
        assert msg.role == "assistant"

    def test_parse_response_tool_use(self):
        p = AnthropicProvider(api_key="sk-ant-test")
        data = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "time_get",
                    "input": {"user_id": "test"},
                },
            ],
            "stop_reason": "tool_use",
        }
        msg = p._parse_response(data)
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "time_get"

    def test_sse_event_parsing(self):
        p = AnthropicProvider(api_key="sk-ant-test")
        current_tc = {}
        events = p._parse_sse_event(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
            current_tc,
        )
        canonical = [e for e in events if e.type == "text_delta"]
        assert len(canonical) == 1
        assert canonical[0].content == "Hi"

    def test_sse_tool_start(self):
        p = AnthropicProvider(api_key="sk-ant-test")
        current_tc = {}
        events = p._parse_sse_event(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "time_get"},
            },
            current_tc,
        )
        canonical = [e for e in events if e.type == "tool_input_start"]
        assert len(canonical) == 1
        assert canonical[0].tool == "time_get"

    def test_get_model_info_claude_sonnet(self):
        p = AnthropicProvider(api_key="sk-ant-test")
        info = p.get_model_info("claude-sonnet-4-20250514")
        assert info.context_window == 200000
        assert info.reasoning is True

    def test_get_model_info_unknown(self):
        p = AnthropicProvider(api_key="sk-ant-test")
        info = p.get_model_info("claude-unknown")
        assert info.provider_id == "anthropic"


# ─── Gemini Provider Tests ───


class TestGeminiProvider:
    def test_default_config(self):
        p = GeminiProvider(api_key="test-key")
        assert p.provider_id == "gemini"
        assert p.model == "gemini-2.5-flash"

    def test_messages_to_contents_system(self):
        p = GeminiProvider(api_key="test-key")
        msgs = [Message.system("Be helpful"), Message.user("Hi")]
        contents = p._messages_to_contents(msgs)
        assert contents[0]["role"] == "user"
        assert "[System]" in contents[0]["parts"][0]["text"]
        assert contents[1]["role"] == "model"

    def test_messages_to_contents_tool_result(self):
        p = GeminiProvider(api_key="test-key")
        msgs = [Message.tool_result("call_1", "12:00", "time_get")]
        contents = p._messages_to_contents(msgs)
        assert contents[0]["role"] == "function"

    def test_tools_to_gemini(self, tool_defs):
        p = GeminiProvider(api_key="test-key")
        result = p._tools_to_gemini(tool_defs)
        assert len(result) == 1
        assert "functionDeclarations" in result[0]
        assert result[0]["functionDeclarations"][0]["name"] == "time_get"

    def test_parse_response_text(self):
        p = GeminiProvider(api_key="test-key")
        data = {
            "candidates": [{"content": {"parts": [{"text": "Hello!"}]}}],
        }
        msg = p._parse_response(data)
        assert msg.content == "Hello!"

    def test_parse_response_function_call(self):
        p = GeminiProvider(api_key="test-key")
        data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"functionCall": {"name": "time_get", "args": {"user_id": "test"}}}
                        ]
                    }
                }
            ],
        }
        msg = p._parse_response(data)
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "time_get"

    def test_parse_response_empty(self):
        p = GeminiProvider(api_key="test-key")
        data = {"candidates": []}
        msg = p._parse_response(data)
        assert msg.content == ""

    def test_get_model_info_flash(self):
        p = GeminiProvider(api_key="test-key")
        info = p.get_model_info("gemini-2.5-flash")
        assert info.context_window == 1048576
        assert info.reasoning is True

    def test_get_model_info_unknown(self):
        p = GeminiProvider(api_key="test-key")
        info = p.get_model_info("gemini-unknown")
        assert info.provider_id == "gemini"


# ─── Factory Tests ───


    def test_stream_chunk_emits_reasoning_canonical_and_alias(self):
        """The OpenAI provider must emit reasoning_delta (canonical) AND
        reasoning (backward-compat alias), matching the other providers —
        consumers that count only canonical types would otherwise miss it."""
        p = OpenAIProvider(api_key="sk-test")

        class Delta:
            content = None
            reasoning_content = "thinking..."
            tool_calls = None

        class Choice:
            delta = Delta()
            finish_reason = None

        class Chunk:
            choices = [Choice()]
            usage = None

        events = p._parse_stream_chunk(Chunk(), {})
        canonical = [e for e in events if e.type == "reasoning_delta"]
        alias = [e for e in events if e.type == "reasoning"]
        assert len(canonical) == 1
        assert len(alias) == 1
        assert canonical[0].content == "thinking..."
        assert alias[0].content == "thinking..."


class TestProviderFactory:
    def test_saved_default_model_is_used_and_explicit_model_wins(self):
        saved = SavedUserSettings(default_model="anthropic:saved-model")

        _, saved_create = _create_with_saved_settings(saved)
        _, explicit_create = _create_with_saved_settings(
            saved, config_model="openai:explicit-model"
        )

        saved_create.assert_called_once_with(
            "anthropic", model="saved-model", api_key=None
        )
        explicit_create.assert_called_once_with(
            "openai", model="explicit-model", api_key=None
        )

    def test_saved_provider_key_is_passed_and_request_key_wins(self):
        saved = SavedUserSettings(
            default_model="openai:gpt-5", provider_keys={"openai": "saved-secret"}
        )

        _, saved_create = _create_with_saved_settings(saved)
        _, request_create = _create_with_saved_settings(
            saved, provider_keys={"openai": "request-secret"}
        )

        saved_create.assert_called_once_with(
            "openai", model="gpt-5", api_key="saved-secret"
        )
        request_create.assert_called_once_with(
            "openai", model="gpt-5", api_key="request-secret"
        )

    @pytest.mark.parametrize("request_key", ["OpenAI", "openai"])
    def test_request_provider_keys_accept_exact_and_lowercase_forms(self, request_key: str):
        saved = SavedUserSettings()

        _, create = _create_with_saved_settings(
            saved,
            config_model="OpenAI:gpt-5",
            provider_keys={request_key: "request-secret"},
        )

        create.assert_called_once_with(
            "openai", model="gpt-5", api_key="request-secret"
        )

    def test_saved_provider_key_wins_over_environment(self):
        saved = SavedUserSettings(
            default_model="openai:gpt-5", provider_keys={"openai": "saved-secret"}
        )
        with patch.dict("os.environ", {"OPENAI_API_KEY": "environment-secret"}):
            _, create = _create_with_saved_settings(saved)

        create.assert_called_once_with("openai", model="gpt-5", api_key="saved-secret")

    def test_missing_settings_use_host_model_and_provider_environment(self):
        store = MagicMock()
        store.load.return_value = SavedUserSettings()
        with (
            patch("src.config.get_settings", return_value=_host_settings("openai:host-model")),
            patch(
                "src.sdk.providers.factory._user_settings_store.UserSettingsStore",
                return_value=store,
            ),
            patch.dict("os.environ", {"OPENAI_API_KEY": "environment-secret"}),
        ):
            provider = create_model_from_config(user_id="alice")

        assert isinstance(provider, OpenAIProvider)
        assert provider.model == "host-model"
        assert provider._client.api_key == "environment-secret"

    def test_legacy_settings_migrate_and_supply_model_and_key(self, tmp_path: Path):
        store = _user_settings_store(tmp_path)
        store.legacy_path.parent.mkdir(parents=True, exist_ok=True)
        store.legacy_path.write_text(
            json.dumps(
                {
                    "default_model": "openai/legacy-model",
                    "provider_keys": {"openai": "legacy-secret"},
                }
            ),
            encoding="utf-8",
        )
        provider = MagicMock()
        with (
            patch("src.config.get_settings", return_value=_host_settings()),
            patch(
                "src.sdk.providers.factory._user_settings_store.UserSettingsStore",
                return_value=store,
            ),
            patch("src.sdk.providers.factory.create_provider_from_registry_model", return_value=None),
            patch("src.sdk.providers.factory.create_provider", return_value=provider) as create,
        ):
            result = create_model_from_config(user_id="alice")

        assert result is provider
        create.assert_called_once_with(
            "openai", model="legacy-model", api_key="legacy-secret"
        )
        assert store.path.exists()
        assert not store.legacy_path.exists()
        assert store.legacy_path.with_name("settings.json.migrated").exists()

    def test_canonical_versioned_settings_supply_model_and_key(self, tmp_path: Path):
        store = _user_settings_store(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "revision": 3,
                    "default_model": "openai:canonical-model",
                    "provider_keys": {"openai": "canonical-secret"},
                    "verification": {},
                }
            ),
            encoding="utf-8",
        )
        provider = MagicMock()
        with (
            patch("src.config.get_settings", return_value=_host_settings()),
            patch(
                "src.sdk.providers.factory._user_settings_store.UserSettingsStore",
                return_value=store,
            ),
            patch("src.sdk.providers.factory.create_provider_from_registry_model", return_value=None),
            patch("src.sdk.providers.factory.create_provider", return_value=provider) as create,
        ):
            result = create_model_from_config(user_id="alice")

        assert result is provider
        create.assert_called_once_with(
            "openai", model="canonical-model", api_key="canonical-secret"
        )

    def test_corrupt_settings_log_redacted_warning_and_fall_back(self, tmp_path: Path):
        secret = b"super-secret-bytes"
        store = _user_settings_store(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_bytes(b'{"provider_keys":{"openai":"' + secret)
        logger = MagicMock()
        provider = MagicMock()
        with (
            patch("src.config.get_settings", return_value=_host_settings("ollama:host-model")),
            patch(
                "src.sdk.providers.factory._user_settings_store.UserSettingsStore",
                return_value=store,
            ),
            patch("src.sdk.providers.factory.logger", logger),
            patch("src.sdk.providers.factory.create_provider_from_registry_model", return_value=None),
            patch("src.sdk.providers.factory.create_provider", return_value=provider) as create,
        ):
            result = create_model_from_config(user_id="alice")

        assert result is provider
        create.assert_called_once_with("ollama", model="host-model", api_key=None)
        logger.warning.assert_called_once_with(
            "provider.user_settings_load_failed",
            {"error_type": "SettingsConfigurationError"},
            user_id="alice",
        )
        assert secret.decode() not in repr(logger.warning.call_args)

    def test_invalid_user_id_falls_back_without_path_escape(self, tmp_path: Path):
        provider = MagicMock()
        logger = MagicMock()
        with (
            patch("src.config.get_settings", return_value=_host_settings("ollama:host-model")),
            patch("src.sdk.providers.factory.logger", logger),
            patch("src.sdk.providers.factory.create_provider_from_registry_model", return_value=None),
            patch("src.sdk.providers.factory.create_provider", return_value=provider),
            patch.dict(
                "os.environ",
                {
                    "DEPLOYMENT_DATA_PATH": str(tmp_path / "project"),
                    "DEPLOYMENT_EA_ROOT": str(tmp_path / "home"),
                },
            ),
        ):
            result = create_model_from_config(user_id="../escape")

        assert result is provider
        assert not (tmp_path / "escape").exists()
        logger.warning.assert_called_once_with(
            "provider.user_settings_load_failed",
            {"error_type": "ValueError"},
            user_id="../escape",
        )

    def test_saved_openrouter_model_with_slash_is_canonical_and_usable(self):
        saved = SavedUserSettings(
            default_model="openrouter:anthropic/claude-sonnet-4",
            provider_keys={"openrouter": "saved-secret"},
        )

        store, create = _create_with_saved_settings(saved)

        assert store.load.call_count == 1
        create.assert_called_once_with(
            "openrouter",
            model="anthropic/claude-sonnet-4",
            api_key="saved-secret",
        )

    def test_create_model_loads_one_settings_snapshot(self):
        saved = SavedUserSettings(
            default_model="openai:gpt-5", provider_keys={"openai": "saved-secret"}
        )

        store, _ = _create_with_saved_settings(saved)

        store.load.assert_called_once_with()

    def test_registry_anthropic_provider_retains_native_class_and_saved_key(self):
        secret = "saved-anthropic-secret"
        store = MagicMock()
        store.load.return_value = SavedUserSettings(provider_keys={"anthropic": secret})

        def registry_create(model_ref: str, api_key: str | None = None):
            return AnthropicProvider(api_key=api_key, model=model_ref.split(":", 1)[1])

        with (
            patch("src.config.get_settings", return_value=_host_settings()),
            patch(
                "src.sdk.providers.factory._user_settings_store.UserSettingsStore",
                return_value=store,
            ),
            patch(
                "src.sdk.providers.factory.create_provider_from_registry_model",
                side_effect=registry_create,
            ) as registry,
        ):
            provider = create_model_from_config("Anthropic:claude-sonnet-4", user_id="alice")

        assert isinstance(provider, AnthropicProvider)
        assert provider.api_key == secret
        registry.assert_called_once_with("anthropic:claude-sonnet-4", api_key=secret)

    def test_registry_gemini_provider_retains_native_class_and_request_key(self):
        secret = "request-gemini-secret"

        def registry_create(model_ref: str, api_key: str | None = None):
            return GeminiProvider(api_key=api_key, model=model_ref.split(":", 1)[1])

        with (
            patch("src.config.get_settings", return_value=_host_settings()),
            patch(
                "src.sdk.providers.factory._user_settings_store.UserSettingsStore"
            ) as store_type,
            patch(
                "src.sdk.providers.factory.create_provider_from_registry_model",
                side_effect=registry_create,
            ),
        ):
            provider = create_model_from_config(
                "Gemini:gemini-2.5-pro",
                provider_keys={"gemini": secret},
                user_id="alice",
            )

        assert isinstance(provider, GeminiProvider)
        assert provider.api_key == secret
        store_type.assert_not_called()

    def test_registry_ollama_cloud_retains_native_class_and_saved_key(self):
        secret = "saved-ollama-secret"
        store = MagicMock()
        store.load.return_value = SavedUserSettings(provider_keys={"OLLAMA-CLOUD": secret})

        def registry_create(model_ref: str, api_key: str | None = None):
            return OllamaCloud(api_key=api_key, model=model_ref.split(":", 1)[1])

        with (
            patch("src.config.get_settings", return_value=_host_settings()),
            patch(
                "src.sdk.providers.factory._user_settings_store.UserSettingsStore",
                return_value=store,
            ),
            patch(
                "src.sdk.providers.factory.create_provider_from_registry_model",
                side_effect=registry_create,
            ),
        ):
            provider = create_model_from_config("ollama-cloud:minimax-m2.5", user_id="alice")

        assert isinstance(provider, OllamaCloud)
        assert provider.api_key == secret

    def test_registry_openai_compatible_provider_is_used_directly(self):
        secret = "request-openrouter-secret"

        def registry_create(model_ref: str, api_key: str | None = None):
            return OpenAIProvider(
                api_key=api_key,
                model=model_ref.split(":", 1)[1],
                provider_id="openrouter",
                base_url="https://openrouter.ai/api/v1",
            )

        with (
            patch("src.config.get_settings", return_value=_host_settings()),
            patch("src.sdk.providers.factory._user_settings_store.UserSettingsStore") as store,
            patch(
                "src.sdk.providers.factory.create_provider_from_registry_model",
                side_effect=registry_create,
            ),
        ):
            provider = create_model_from_config(
                "OpenRouter:anthropic/claude-sonnet-4",
                provider_keys={"OpenRouter": secret},
                user_id="alice",
            )

        assert isinstance(provider, OpenAIProvider)
        assert provider.provider_id == "openrouter"
        assert provider.model == "anthropic/claude-sonnet-4"
        assert provider._client.api_key == secret
        store.assert_not_called()

    def test_explicit_model_and_matching_request_key_skip_user_settings_store(self):
        provider = MagicMock()
        with (
            patch("src.config.get_settings", return_value=_host_settings()),
            patch("src.sdk.providers.factory._user_settings_store.UserSettingsStore") as store,
            patch("src.sdk.providers.factory.create_provider_from_registry_model", return_value=None),
            patch("src.sdk.providers.factory.create_provider", return_value=provider),
        ):
            result = create_model_from_config(
                "OpenAI:gpt-4.1",
                provider_keys={"openai": "request-secret"},
                user_id="alice",
            )

        assert result is provider
        store.assert_not_called()

    def test_explicit_model_without_matching_request_key_loads_saved_key_once(self):
        store = MagicMock()
        store.load.return_value = SavedUserSettings(provider_keys={"openai": "saved-secret"})
        with (
            patch("src.config.get_settings", return_value=_host_settings()),
            patch(
                "src.sdk.providers.factory._user_settings_store.UserSettingsStore",
                return_value=store,
            ) as store_type,
            patch("src.sdk.providers.factory.create_provider_from_registry_model", return_value=None),
            patch("src.sdk.providers.factory.create_provider") as create,
        ):
            create_model_from_config("OpenAI:gpt-4.1", user_id="alice")

        store_type.assert_called_once_with("alice")
        store.load.assert_called_once_with()
        create.assert_called_once_with("openai", model="gpt-4.1", api_key="saved-secret")

    def test_missing_model_loads_one_snapshot_for_saved_default_and_key(self):
        store = MagicMock()
        store.load.return_value = SavedUserSettings(
            default_model="OpenAI:gpt-4.1", provider_keys={"openai": "saved-secret"}
        )
        with (
            patch("src.config.get_settings", return_value=_host_settings()),
            patch(
                "src.sdk.providers.factory._user_settings_store.UserSettingsStore",
                return_value=store,
            ),
            patch("src.sdk.providers.factory.create_provider_from_registry_model", return_value=None),
            patch("src.sdk.providers.factory.create_provider") as create,
        ):
            create_model_from_config(user_id="alice")

        store.load.assert_called_once_with()
        create.assert_called_once_with("openai", model="gpt-4.1", api_key="saved-secret")

    def test_settings_storage_oserror_logs_redacted_and_falls_back_to_host_and_env(self):
        secret = "filesystem-secret"
        logger = MagicMock()
        with (
            patch("src.config.get_settings", return_value=_host_settings("OpenAI:host-model")),
            patch(
                "src.sdk.providers.factory._user_settings_store.UserSettingsStore",
                side_effect=OSError(secret),
            ),
            patch("src.sdk.providers.factory.create_provider_from_registry_model", return_value=None),
            patch("src.sdk.providers.factory.logger", logger),
            patch.dict("os.environ", {"OPENAI_API_KEY": "environment-secret"}),
        ):
            provider = create_model_from_config(user_id="alice")

        assert isinstance(provider, OpenAIProvider)
        assert provider.model == "host-model"
        assert provider._client.api_key == "environment-secret"
        logger.warning.assert_called_once_with(
            "provider.user_settings_load_failed",
            {"error_type": "OSError"},
            user_id="alice",
        )
        assert secret not in repr(logger.method_calls)

    def test_uppercase_model_provider_uses_lowercase_saved_key_and_registry_ref(self):
        secret = "saved-openai-secret"
        store = MagicMock()
        store.load.return_value = SavedUserSettings(provider_keys={"openai": secret})
        provider = MagicMock()
        with (
            patch("src.config.get_settings", return_value=_host_settings()),
            patch(
                "src.sdk.providers.factory._user_settings_store.UserSettingsStore",
                return_value=store,
            ),
            patch(
                "src.sdk.providers.factory.create_provider_from_registry_model",
                return_value=provider,
            ) as registry,
        ):
            result = create_model_from_config("OpenAI:gpt-4.1", user_id="alice")

        assert result is provider
        registry.assert_called_once_with("openai:gpt-4.1", api_key=secret)

    def test_uppercase_model_provider_uses_lowercase_environment_lookup(self):
        store = MagicMock()
        store.load.return_value = SavedUserSettings()
        with (
            patch("src.config.get_settings", return_value=_host_settings()),
            patch(
                "src.sdk.providers.factory._user_settings_store.UserSettingsStore",
                return_value=store,
            ),
            patch("src.sdk.providers.factory.create_provider_from_registry_model", return_value=None),
            patch.dict("os.environ", {"OPENAI_API_KEY": "environment-secret"}),
        ):
            provider = create_model_from_config("OpenAI:gpt-4.1", user_id="alice")

        assert isinstance(provider, OpenAIProvider)
        assert provider.provider_id == "openai"
        assert provider.model == "gpt-4.1"
        assert provider._client.api_key == "environment-secret"

    def test_parse_model_string_with_provider(self):
        provider, model = _parse_model_string("openai:gpt-4o")
        assert provider == "openai"
        assert model == "gpt-4o"

    def test_parse_model_string_with_slash_provider(self):
        provider, model = _parse_model_string("anthropic/claude-sonnet-4-5")
        assert provider == "anthropic"
        assert model == "claude-sonnet-4-5"

    def test_parse_model_string_colon_preserves_model_slashes(self):
        provider, model = _parse_model_string("openrouter:anthropic/claude-sonnet-4")
        assert provider == "openrouter"
        assert model == "anthropic/claude-sonnet-4"

    def test_parse_model_string_without_provider(self):
        provider, model = _parse_model_string("minimax-m2.5")
        assert provider == "ollama"
        assert model == "minimax-m2.5"

    def test_create_ollama_provider_always_local_regardless_of_env(self):
        with patch.dict(
            "os.environ",
            {"OLLAMA_BASE_URL": "https://ollama.com", "OLLAMA_API_KEY": "test-key"},
        ):
            p = create_provider("ollama", model="gemma4:e4b")
            assert isinstance(p, OpenAIProvider)
            assert p.provider_id == "ollama"
            assert p.model == "gemma4:e4b"

    def test_create_ollama_cloud_provider(self):
        p = create_provider("ollama-cloud", model="minimax-m2.5", api_key="test")
        assert isinstance(p, OllamaCloud)
        assert p.model == "minimax-m2.5"
        assert str(p.base_url) == "https://ollama.com"

    def test_create_openai_provider(self):
        p = create_provider("openai", model="gpt-4o", api_key="sk-test")
        assert isinstance(p, OpenAIProvider)

    def test_create_agnes_provider(self):
        p = create_provider("agnes", model="agnes-2.0-flash", api_key="sk-test")

        assert isinstance(p, OpenAIProvider)
        assert p.provider_id == "agnes"
        assert p.model == "agnes-2.0-flash"
        assert p._extract_provider_options(None) == {
            "chat_template_kwargs": {"enable_thinking": True}
        }

    def test_create_anthropic_provider(self):
        p = create_provider("anthropic", model="claude-sonnet-4-20250514", api_key="sk-ant-test")
        assert isinstance(p, AnthropicProvider)

    def test_create_gemini_provider(self):
        p = create_provider("gemini", model="gemini-2.5-pro", api_key="test-key")
        assert isinstance(p, GeminiProvider)

    def test_create_openai_compatible_provider(self):
        p = create_provider(
            "groq",
            model="llama-3.1-70b-versatile",
            api_key="gsk-test",
        )
        assert isinstance(p, OpenAIProvider)
        assert p.provider_id == "groq"

    def test_create_deepseek_provider(self):
        p = create_provider("deepseek", model="deepseek-chat", api_key="sk-test")
        assert isinstance(p, OpenAIProvider)

    def test_create_openrouter_provider(self):
        p = create_provider("openrouter", model="anthropic/claude-sonnet-4", api_key="sk-or-test")
        assert isinstance(p, OpenAIProvider)

    def test_unknown_provider_creates_openai_compatible(self):
        p = create_provider(
            "unknown-ai", model="test", api_key="sk-test", base_url="https://api.unknown.ai/v1"
        )
        assert isinstance(p, OpenAIProvider)

    @patch.dict("os.environ", {"AGENT_MODEL": "openai:gpt-4o"})
    def test_create_model_from_config_with_env(self):
        with patch("src.config.get_settings") as mock_settings:
            mock_settings.return_value.agent.model = "openai:gpt-4o"
            p = create_model_from_config("openai:gpt-4o")
            assert isinstance(p, OpenAIProvider)

    def test_create_model_from_config_ollama(self):
        with patch.dict("os.environ", {"OLLAMA_BASE_URL": "", "OLLAMA_API_KEY": ""}):
            with patch("src.config.get_settings") as mock_settings:
                mock_settings.return_value.agent.model = "ollama:minimax-m2.5"
                p = create_model_from_config("ollama:minimax-m2.5")
                assert isinstance(p, OpenAIProvider)
                assert p.provider_id == "ollama"

    def test_create_model_from_config_ollama_stays_local_with_cloud_env(self):
        with patch.dict(
            "os.environ",
            {"OLLAMA_BASE_URL": "https://ollama.com", "OLLAMA_API_KEY": "test"},
        ):
            p = create_model_from_config("ollama:minimax-m2.5")
            assert isinstance(p, OpenAIProvider)

    def test_create_model_from_config_explicit_ollama_cloud(self):
        with patch.dict("os.environ", {"OLLAMA_API_KEY": "test"}):
            p = create_model_from_config("ollama-cloud/minimax-m2.5")

        assert isinstance(p, OllamaCloud)
        assert p.model == "minimax-m2.5"
        assert str(p.base_url) == "https://ollama.com"

    def test_create_model_from_config_explicit_ollama_cloud_colon_model(self):
        with patch.dict(
            "os.environ",
            {"OLLAMA_BASE_URL": "https://ollama.com", "OLLAMA_API_KEY": "test"},
        ):
            p = create_model_from_config("ollama-cloud:deepseek-v4-flash:cloud")

        assert isinstance(p, OllamaCloud)
        assert p.model == "deepseek-v4-flash:cloud"
        assert str(p.base_url) == "https://ollama.com"

    def test_create_model_from_config_agnes_uses_env_key(self):
        with patch.dict("os.environ", {"AGNES_API_KEY": "sk-agnes-test"}):
            p = create_model_from_config("agnes:agnes-2.0-flash")

        assert isinstance(p, OpenAIProvider)
        assert p.provider_id == "agnes"
        assert p.model == "agnes-2.0-flash"

    def test_create_provider_from_registry_model_uses_models_dev_provider(self):
        with patch.dict("os.environ", {"OLLAMA_API_KEY": "test"}):
            p = create_provider_from_registry_model("ollama-cloud/minimax-m2.5")
            assert isinstance(p, OllamaCloud)

    def test_create_model_from_config_models_dev_colon_uses_registry_provider_env(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test"}, clear=True):
            p = create_model_from_config("openrouter:openai/gpt-4o-mini")

        assert isinstance(p, OpenAIProvider)
        assert p.provider_id == "openrouter"
        assert p.model == "openai/gpt-4o-mini"

    def test_create_model_from_config_models_dev_slash_preserves_provider_identity(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test"}, clear=True):
            p = create_model_from_config("openrouter/openai/gpt-4o-mini")

        assert isinstance(p, OpenAIProvider)
        assert p.provider_id == "openrouter"
        assert p.model == "openai/gpt-4o-mini"

    def test_create_model_from_config_models_dev_xai_uses_registry_env_key(self):
        with patch.dict("os.environ", {"XAI_API_KEY": "xai-test"}, clear=True):
            p = create_model_from_config("xai:grok-4")

        assert isinstance(p, OpenAIProvider)
        assert p.provider_id == "xai"
        assert p.model == "grok-4"

    def test_legacy_native_ollama_cloud_emits_thinking(self):
        p = OllamaCloud(model="minimax-m2.5", api_key="test")
        chunks = p._parse_chunk(
            {
                "model": "minimax-m2.5",
                "message": {"thinking": "hidden", "content": ""},
                "done": False,
            },
            {},
        )
        assert [c.type for c in chunks] == ["reasoning_delta", "reasoning"]



# ─── Message Format Conversion Tests ───


class TestMessageConversion:
    def test_openai_format_roundtrip(self):
        msgs = [
            Message.system("Be helpful"),
            Message.user("What time is it?"),
        ]
        openai_msgs = [m.to_openai() for m in msgs]
        assert openai_msgs[0]["role"] == "system"
        assert openai_msgs[1]["role"] == "user"
        roundtrip = [Message.from_openai(m) for m in openai_msgs]
        assert roundtrip[0].role == "system"
        assert roundtrip[1].role == "user"

    def test_anthropic_format_system(self):
        msg = Message.system("Be helpful")
        anth = msg.to_anthropic()
        assert anth["type"] == "text"

    def test_anthropic_format_tool_result(self):
        msg = Message.tool_result("call_1", "12:00", "time_get")
        anth = msg.to_anthropic()
        assert anth["role"] == "user"
        assert anth["content"][0]["type"] == "tool_result"

    def test_tool_definition_openai_format(self, tool_defs):
        fmt = tool_defs[0].to_openai_format()
        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "time_get"

    def test_tool_definition_anthropic_format(self, tool_defs):
        fmt = AnthropicProvider._to_anthropic_tool(tool_defs[0])
        assert fmt["name"] == "time_get"
        assert "input_schema" in fmt
class TestOpenAIUsageExtraction:
    def test_parse_response_extracts_usage(self):
        p = OpenAIProvider(api_key="test")
        data = MagicMock()
        data.choices = [MagicMock()]
        data.choices[0].message.content = "Hello"
        data.choices[0].message.tool_calls = None
        data.usage.prompt_tokens = 100
        data.usage.completion_tokens = 50
        data.usage.completion_tokens_details = None
        data.usage.prompt_tokens_details = None
        msg = p._parse_response(data)
        assert msg.usage is not None
        assert msg.usage.input_tokens == 100
        assert msg.usage.output_tokens == 50

    def test_parse_response_no_usage(self):
        p = OpenAIProvider(api_key="test")
        data = MagicMock()
        data.choices = [MagicMock()]
        data.choices[0].message.content = "Hello"
        data.choices[0].message.tool_calls = None
        data.usage = None
        msg = p._parse_response(data)
        assert msg.usage is None

    def test_stream_chunk_extracts_usage(self):
        p = OpenAIProvider(api_key="test")
        chunk = MagicMock()
        chunk.choices = []
        chunk.usage.prompt_tokens = 200
        chunk.usage.completion_tokens = 80
        chunk.usage.completion_tokens_details = None
        chunk.usage.prompt_tokens_details = None
        events = p._parse_stream_chunk(chunk, {})
        usage_events = [e for e in events if e.type == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0].usage.input_tokens == 200
        assert usage_events[0].usage.output_tokens == 80


class TestAnthropicUsageExtraction:
    def test_parse_response_extracts_usage(self):
        p = AnthropicProvider(api_key="test")
        data = {
            "content": [{"type": "text", "text": "Hello"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 150, "output_tokens": 60},
        }
        msg = p._parse_response(data)
        assert msg.content == "Hello"
        assert msg.usage is not None
        assert msg.usage.input_tokens == 150
        assert msg.usage.output_tokens == 60

    def test_parse_response_extracts_cache_usage(self):
        p = AnthropicProvider(api_key="test")
        data = {
            "content": [{"type": "text", "text": "Hello"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 150,
                "output_tokens": 60,
                "cache_read_input_tokens": 30,
                "cache_creation_input_tokens": 10,
            },
        }
        msg = p._parse_response(data)
        assert msg.usage.cache_read_tokens == 30
        assert msg.usage.cache_creation_tokens == 10

    def test_sse_message_start_extracts_usage(self):
        p = AnthropicProvider(api_key="test")
        data = {
            "type": "message_start",
            "message": {
                "usage": {"input_tokens": 500, "output_tokens": 0},
            },
        }
        events = p._parse_sse_event(data, {})
        usage_events = [e for e in events if e.type == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0].usage.input_tokens == 500

    def test_sse_message_delta_extracts_usage(self):
        p = AnthropicProvider(api_key="test")
        data = {
            "type": "message_delta",
            "usage": {"output_tokens": 120},
        }
        events = p._parse_sse_event(data, {})
        usage_events = [e for e in events if e.type == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0].usage.output_tokens == 120

    def test_parse_response_no_usage(self):
        p = AnthropicProvider(api_key="test")
        data = {
            "content": [{"type": "text", "text": "Hello"}],
            "stop_reason": "end_turn",
        }
        msg = p._parse_response(data)
        assert msg.usage is None


class TestGeminiUsageExtraction:
    def test_parse_response_extracts_usage(self):
        p = GeminiProvider(api_key="test")
        data = {
            "candidates": [{"content": {"parts": [{"text": "Hello"}]}}],
            "usageMetadata": {
                "promptTokenCount": 300,
                "candidatesTokenCount": 70,
                "thoughtsTokenCount": 20,
            },
        }
        msg = p._parse_response(data)
        assert msg.usage is not None
        assert msg.usage.input_tokens == 300
        assert msg.usage.output_tokens == 70
        assert msg.usage.reasoning_tokens == 20

    def test_stream_chunk_extracts_usage(self):
        p = GeminiProvider(api_key="test")
        data = {
            "candidates": [{"content": {"parts": [{"text": "Hi"}]}, "finishReason": "STOP"}],
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 40,
            },
        }
        events = p._parse_stream_chunk(data, {})
        usage_events = [e for e in events if e.type == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0].usage.input_tokens == 100
        assert usage_events[0].usage.output_tokens == 40


class TestOpenAIProviderUsageExtraction:
    def test_parse_response_extracts_usage(self):
        from unittest.mock import MagicMock
        p = OpenAIProvider()
        choice = MagicMock()
        choice.message = MagicMock(role="assistant", content="Hi")
        response = MagicMock()
        response.choices = [choice]
        response.usage = MagicMock(prompt_tokens=80, completion_tokens=30)
        msg = p._parse_response(response)
        assert msg.usage is not None
        assert msg.usage.input_tokens == 80
        assert msg.usage.output_tokens == 30

    def test_parse_response_no_usage(self):
        from unittest.mock import MagicMock
        p = OpenAIProvider()
        choice = MagicMock()
        choice.message = MagicMock(role="assistant", content="Hi")
        response = MagicMock()
        response.choices = [choice]
        response.usage = None
        msg = p._parse_response(response)
        assert msg.usage is None

    def test_stream_chunk_extracts_usage(self):
        from unittest.mock import MagicMock
        p = OpenAIProvider()
        delta = MagicMock()
        delta.content = "Hi"
        choice = MagicMock()
        choice.delta = delta
        choice.finish_reason = "stop"
        chunk = MagicMock()
        chunk.choices = [choice]
        chunk.usage = MagicMock(prompt_tokens=80, completion_tokens=30)
        events = p._parse_stream_chunk(chunk, {})
        usage_events = [e for e in events if e.type == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0].usage.input_tokens == 80


class TestOllamaCloudUsageExtraction:
    def test_parse_response_extracts_usage_dict(self):
        p = OllamaCloud(api_key="test")
        data = {
            "message": {"role": "assistant", "content": "Hi"},
            "usage": {"prompt_tokens": 90, "completion_tokens": 40},
        }
        msg = p._parse_response(data)
        assert msg.usage is not None
        assert msg.usage.input_tokens == 90
        assert msg.usage.output_tokens == 40

    def test_parse_response_extracts_usage_native_fields(self):
        p = OllamaCloud(api_key="test")
        data = {
            "message": {"role": "assistant", "content": "Hi"},
            "prompt_eval_count": 90,
            "eval_count": 40,
        }
        msg = p._parse_response(data)
        assert msg.usage is not None
        assert msg.usage.input_tokens == 90
        assert msg.usage.output_tokens == 40

    def test_parse_response_no_usage(self):
        p = OllamaCloud(api_key="test")
        data = {"message": {"role": "assistant", "content": "Hi"}}
        msg = p._parse_response(data)
        assert msg.usage is None
