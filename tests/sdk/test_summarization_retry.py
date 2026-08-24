"""Regression tests for the audit B14 summarization robustness bundle.

- the transient retry policy actually catches provider timeout errors
  (previously it caught builtin exceptions no provider raises — dead code)
- the previous-summary framing strip removes every framing layer
- per-user prompt seeding is atomic under crash and concurrency
- a failing persistence sink stashes a degraded summary and retries
  persistence on the next successful sink (provenance invariant, plan §15)
"""

import asyncio
import types
from pathlib import Path

import httpx
import pytest

from src.sdk.compression import (
    CompressionContext,
    CompressionStatus,
    PersistenceStatus,
    SummaryPersistenceResult,
)
from src.sdk.messages import Message
from src.sdk.middleware_summarization import (
    SUMMARY_MESSAGE_PREFIX,
    SummarizationMiddleware,
    _load_prompt_file,
)
from src.sdk.state import AgentState


def _context(session_id: str = "session-1") -> CompressionContext:
    return CompressionContext(
        session_id=session_id,
        model="ollama-cloud:test",
        attempt=1,
        llm_call_index=1,
        reason="manual",
    )


def _storied_messages(count: int = 6) -> list[Message]:
    """Messages carrying storage_ids so persistence is eligible."""
    return [
        Message(role="user", content=f"user-{i}", storage_id=f"m{i}")
        for i in range(count)
    ]


class _OkProvider:
    def __init__(self, content: str = "summary"):
        self.content = content
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        return Message.assistant(self.content)


class _FlakyProvider:
    """Raises an httpx timeout once, then succeeds."""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        if self.calls == 1:
            raise httpx.ReadTimeout("boom")
        return Message.assistant("retried summary")


class _FailingThenSucceedingSink:
    def __init__(self):
        self.calls = 0
        self.artifacts: list[object] = []

    async def __call__(self, context, artifact):
        self.calls += 1
        self.artifacts.append(artifact)
        if self.calls == 1:
            raise OSError("sink down")
        return SummaryPersistenceResult(
            status=PersistenceStatus.SUCCEEDED, summary_id=f"sid-{self.calls}"
        )


def _make_middleware(provider, **kwargs) -> SummarizationMiddleware:
    return SummarizationMiddleware(
        "ollama-cloud:test",
        summary_provider_factory=lambda: provider,
        **kwargs,
    )


async def _compress(mw: SummarizationMiddleware, messages: list[Message], session_id: str = "session-1"):
    state = AgentState(
        messages=list(messages), extra={"_compression_context": _context(session_id)}
    )
    result = await mw.force_summarize(state, _context(session_id))
    return result, state


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_retries_once_on_httpx_timeout():
    """httpx.ReadTimeout must trigger the one-shot retry (was dead code)."""
    provider = _FlakyProvider()
    mw = SummarizationMiddleware("ollama-cloud:test")
    messages = [Message.user("summarize this")]

    result = await mw._call_summary_provider_with_retry(provider, messages)

    assert provider.calls == 2
    assert result.content == "retried summary"


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("r"),
        httpx.ConnectTimeout("c"),
        httpx.WriteTimeout("w"),
        httpx.PoolTimeout("p"),
    ],
)
@pytest.mark.asyncio
async def test_summary_retries_on_all_httpx_timeout_kinds(exc):
    class _Once:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise exc
            return Message.assistant("ok")

    provider = _Once()
    mw = SummarizationMiddleware("ollama-cloud:test")
    result = await mw._call_summary_provider_with_retry(
        provider, [Message.user("x")]
    )
    assert provider.calls == 2
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_summary_deterministic_error_propagates_without_retry():
    class _Broken:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages):
            self.calls += 1
            raise ValueError("deterministic")

    provider = _Broken()
    mw = SummarizationMiddleware("ollama-cloud:test")
    with pytest.raises(ValueError, match="deterministic"):
        await mw._call_summary_provider_with_retry(provider, [Message.user("x")])
    assert provider.calls == 1


# ---------------------------------------------------------------------------
# Framing strip
# ---------------------------------------------------------------------------


def test_previous_summary_strip_removes_all_framing_layers():
    mw = SummarizationMiddleware("ollama-cloud:test")
    # Storage framing wraps the in-memory framing; both must be stripped.
    content = (
        "[SUMMARY OF PREVIOUS CONVERSATION]\n"
        f"{SUMMARY_MESSAGE_PREFIX}\n\nreal summary"
    )
    msg = Message(role="user", content=content, source="summarization_middleware")
    assert mw._extract_previous_summary(msg) == "real summary"

    # Plain in-memory framing still works.
    msg2 = Message(
        role="user",
        content=f"{SUMMARY_MESSAGE_PREFIX}\n\nonly this",
        source="summarization_middleware",
    )
    assert mw._extract_previous_summary(msg2) == "only this"


# ---------------------------------------------------------------------------
# Atomic prompt seeding
# ---------------------------------------------------------------------------


def _seed_prompt_text() -> str:
    seed_path = (
        Path(__file__).resolve().parent.parent.parent
        / "seeds"
        / "prompts"
        / "summarisation_prompt.md"
    )
    return seed_path.read_text()


def test_prompt_seeding_is_atomic_under_crash(tmp_path, monkeypatch):
    """A crash mid-seed must never leave a partial target file."""
    fake_settings = types.SimpleNamespace(data_path=str(tmp_path))
    monkeypatch.setattr("src.config.get_settings", lambda: fake_settings)
    seed = _seed_prompt_text()

    real_write = Path.write_text

    def crash_on_target(self, data, *args, **kwargs):
        # Simulate a crash while writing DIRECTLY to the final path (the
        # pre-fix behaviour). The fixed code writes a temp file first and
        # os.replace()s it, so this branch is never reached.
        if str(self).endswith("summarisation_prompt.md"):
            real_write(self, data[:16], *args, **kwargs)
            raise OSError("disk full")
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", crash_on_target)

    result = _load_prompt_file("summarisation_prompt.md", user_id="u1")

    assert result == seed
    target = tmp_path / "users" / "u1" / "summarisation_prompt.md"
    assert target.read_text() == seed


def test_prompt_seeding_concurrent_readers_never_see_partial(tmp_path, monkeypatch):
    fake_settings = types.SimpleNamespace(data_path=str(tmp_path))
    monkeypatch.setattr("src.config.get_settings", lambda: fake_settings)
    seed = _seed_prompt_text()

    async def worker() -> str:
        return _load_prompt_file("summarisation_prompt.md", user_id="u1")

    async def run() -> list[str]:
        return await asyncio.gather(*[worker() for _ in range(30)])

    results = asyncio.run(run())
    assert all(r == seed for r in results)


# ---------------------------------------------------------------------------
# Sink failure: degraded stash + retry persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sink_failure_stashes_degraded_summary():
    sink = _FailingThenSucceedingSink()
    mw = _make_middleware(_OkProvider(), keep=("messages", 2), summary_sink=sink)

    result, _ = await _compress(mw, _storied_messages())

    assert result.telemetry.status is CompressionStatus.SUCCEEDED
    assert result.telemetry.persistence.status is PersistenceStatus.FAILED
    # The generated summary is stashed so a later sink can persist it.
    assert mw._degraded_summaries.get("session-1") is not None


@pytest.mark.asyncio
async def test_degraded_summary_retried_on_next_successful_sink():
    sink = _FailingThenSucceedingSink()
    mw = _make_middleware(_OkProvider(), keep=("messages", 2), summary_sink=sink)

    result1, _ = await _compress(mw, _storied_messages())
    assert result1.telemetry.persistence.status is PersistenceStatus.FAILED
    assert "session-1" in mw._degraded_summaries
    first_calls = sink.calls

    # Second compress on the same session: sink works. The stashed summary
    # must be re-persisted first and the degraded entry cleared.
    result2, _ = await _compress(mw, _storied_messages())
    assert result2.telemetry.persistence.status is PersistenceStatus.SUCCEEDED
    assert "session-1" not in mw._degraded_summaries
    assert sink.calls >= first_calls + 2  # stale retry + fresh persist


@pytest.mark.asyncio
async def test_degraded_summary_kept_when_retry_fails_again():
    class _AlwaysFailingSink:
        def __init__(self):
            self.calls = 0

        async def __call__(self, context, artifact):
            self.calls += 1
            return SummaryPersistenceResult(status=PersistenceStatus.FAILED)

    sink = _AlwaysFailingSink()
    mw = _make_middleware(_OkProvider(), keep=("messages", 2), summary_sink=sink)

    await _compress(mw, _storied_messages())
    assert "session-1" in mw._degraded_summaries

    # A second attempt with a still-failing sink must NOT lose the stash.
    result2, _ = await _compress(mw, _storied_messages())
    assert result2.telemetry.persistence.status is PersistenceStatus.FAILED
    assert "session-1" in mw._degraded_summaries
