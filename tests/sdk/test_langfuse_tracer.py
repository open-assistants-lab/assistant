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
