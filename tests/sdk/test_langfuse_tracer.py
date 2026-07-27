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
