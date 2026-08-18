"""Unit tests for LangfuseTracer."""

from src.sdk.langfuse_tracer import LangfuseTracer


def test_is_enabled_false_when_not_initialized():
    LangfuseTracer._client = None
    assert LangfuseTracer.is_enabled() is False


def test_score_current_trace_noop_when_disabled():
    LangfuseTracer._client = None
    # Should not raise
    LangfuseTracer.score_current_trace(name="test", value=1.0)


def test_flush_noop_when_disabled():
    LangfuseTracer._client = None
    # Should not raise
    LangfuseTracer.flush()


def test_wrap_provider_returns_provider_when_disabled():
    LangfuseTracer._client = None

    class FakeProvider:
        async def chat(self, messages, **kwargs):
            return type("M", (), {"role": "assistant", "content": "hi"})()

    original = FakeProvider()
    wrapped = LangfuseTracer.wrap_provider(original)
    # When disabled, should return original unchanged
    assert wrapped is original


def test_wrap_loop_returns_loop_when_disabled():
    LangfuseTracer._client = None

    class FakeLoop:
        async def run(self, messages):
            return []

    original = FakeLoop()
    wrapped = LangfuseTracer.wrap_loop(original, user_id="u", session_id="s")
    # When disabled, should return original unchanged
    assert wrapped is original


def test_factory_wraps_provider_when_enabled(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

    import src.config.settings as _cfg
    _cfg._config = None

    LangfuseTracer._client = None

    from src.sdk.providers.factory import create_model_from_config
    provider = create_model_from_config("ollama-cloud:test-model", user_id="test")

    # Provider should still work (wrapping is transparent)
    assert provider is not None
    assert LangfuseTracer.is_enabled() is True

    LangfuseTracer._client = None
    _cfg._config = None


def test_wrap_provider_stream_records_output_and_ttft(monkeypatch):
    """The streaming wrapper must attach the final output (content +
    reasoning) and completion_start_time (TTFT) to the generation, and
    accumulate usage across chunks."""
    import asyncio
    from datetime import datetime

    from src.sdk.messages import StreamChunk, Usage

    class FakeGen:
        def __init__(self):
            self.updates: list[dict] = []
            self.ended = False

        def update(self, **kwargs):
            self.updates.append(kwargs)

        def end(self):
            self.ended = True

    class FakeClient:
        def __init__(self):
            self.gen = FakeGen()

        def start_observation(self, name, as_type, **kw):
            return self.gen

    class FakeProvider:
        async def chat(self, messages, **kwargs):
            return None

        async def chat_stream(self, messages, tools=None, model=None, provider_options=None, **kwargs):
            yield StreamChunk.reasoning_delta(content="Let me think")
            yield StreamChunk.text_delta(content="Hello")
            yield StreamChunk.usage_event(Usage(input_tokens=10, output_tokens=5, reasoning_tokens=3))

    client = FakeClient()
    monkeypatch.setattr(LangfuseTracer, "_client", object())
    monkeypatch.setattr(LangfuseTracer, "_get_client", classmethod(lambda cls: client))

    wrapped = LangfuseTracer.wrap_provider(FakeProvider())

    async def collect():
        out = []
        async for c in wrapped.chat_stream([], model="m"):
            out.append(c)
        return out

    chunks = asyncio.run(collect())
    assert len(chunks) == 3  # passthrough unchanged
    assert client.gen.ended

    final = client.gen.updates[-1]
    assert final["output"] == {"role": "assistant", "content": "Hello", "reasoning": "Let me think"}
    assert final["usage_details"] == {"input": 10, "output": 5, "reasoning": 3}
    assert isinstance(final["completion_start_time"], datetime)


def test_wrap_provider_stream_no_content_no_ttft(monkeypatch):
    """A stream with no content chunks records no output and no TTFT."""
    import asyncio

    from src.sdk.messages import StreamChunk

    class FakeGen:
        def __init__(self):
            self.updates: list[dict] = []
            self.ended = False

        def update(self, **kwargs):
            self.updates.append(kwargs)

        def end(self):
            self.ended = True

    class FakeClient:
        def __init__(self):
            self.gen = FakeGen()

        def start_observation(self, name, as_type, **kw):
            return self.gen

    class FakeProvider:
        async def chat(self, messages, **kwargs):
            return None

        async def chat_stream(self, messages, tools=None, model=None, provider_options=None, **kwargs):
            yield StreamChunk.done(content="")

    client = FakeClient()
    monkeypatch.setattr(LangfuseTracer, "_client", object())
    monkeypatch.setattr(LangfuseTracer, "_get_client", classmethod(lambda cls: client))

    wrapped = LangfuseTracer.wrap_provider(FakeProvider())

    async def collect():
        async for _ in wrapped.chat_stream([], model="m"):
            pass

    asyncio.run(collect())
    final = client.gen.updates[-1]
    assert "output" not in final
    assert "completion_start_time" not in final


def test_otel_detach_filter_drops_cross_context_noise() -> None:
    """The OTel detach filter must drop the 'Failed to detach context'
    traceback (expected async-generator teardown noise, swallowed by OTel
    itself) while keeping every other log record."""
    import logging

    from src.sdk.langfuse_tracer import _OtelDetachFilter

    filt = _OtelDetachFilter()
    noisy = logging.LogRecord(
        "opentelemetry.context", logging.ERROR, "ctx.py", 155,
        "Failed to detach context", None, None,
    )
    normal = logging.LogRecord(
        "opentelemetry.context", logging.ERROR, "ctx.py", 1,
        "some other error", None, None,
    )
    assert not filt.filter(noisy)
    assert filt.filter(normal)


def test_otel_detach_filter_installed_on_init(monkeypatch) -> None:
    """LangfuseTracer.init must install the filter on the opentelemetry.context
    logger so the expected teardown traceback never reaches the log."""
    import logging

    from src.sdk.langfuse_tracer import LangfuseTracer, _OtelDetachFilter

    langfuse_logger = logging.getLogger("opentelemetry.context")
    installed = [f for f in langfuse_logger.filters if isinstance(f, _OtelDetachFilter)]
    try:
        LangfuseTracer.init("pk-test", "sk-test", "http://localhost:3000")
        assert any(isinstance(f, _OtelDetachFilter) for f in langfuse_logger.filters)
    finally:
        for f in langfuse_logger.filters:
            if isinstance(f, _OtelDetachFilter) and f not in installed:
                langfuse_logger.removeFilter(f)


def test_wrap_provider_uses_langfuse_name_override(monkeypatch):
    """A reserved provider_options key names the generation (e.g. title)."""
    import asyncio

    from src.sdk.messages import StreamChunk

    class FakeGen:
        def __init__(self):
            self.updates: list[dict] = []
            self.ended = False

        def update(self, **kwargs):
            self.updates.append(kwargs)

        def end(self):
            self.ended = True

    class FakeClient:
        def __init__(self):
            self.gen = FakeGen()
            self.gen_name = None

        def start_observation(self, name, as_type, **kw):
            self.gen_name = name
            return self.gen

    class FakeProvider:
        async def chat(self, messages, **kwargs):
            return None

        async def chat_stream(self, messages, tools=None, model=None, provider_options=None, **kwargs):
            yield StreamChunk.done(content="")

    client = FakeClient()
    monkeypatch.setattr(LangfuseTracer, "_client", object())
    monkeypatch.setattr(LangfuseTracer, "_get_client", classmethod(lambda cls: client))

    wrapped = LangfuseTracer.wrap_provider(FakeProvider())

    async def collect():
        async for _ in wrapped.chat_stream([], model="m", provider_options={"langfuse": {"name": "title_generation"}}):
            pass

    asyncio.run(collect())
    assert client.gen_name == "title_generation"
