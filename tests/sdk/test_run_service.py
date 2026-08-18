"""Tests for RunService with scripted orchestration fixture."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from src.sdk.messages import Message, StreamChunk
from src.sdk.run_service import RunService
from src.sdk.session_worker import SessionBusyError, SessionWorkerRegistry


class InMemoryMessageStore:
    """Minimal in-memory message store for testing."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._id_counter = 0

    def add_message(self, role: str, content: str, metadata: dict | None = None, session_id: str = "") -> str:
        self._id_counter += 1
        mid = f"msg-{self._id_counter}"
        self.messages.append({
            "id": mid,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "session_id": session_id,
        })
        return mid

    def get_messages_with_summary(self, session_id: str, limit: int = 50) -> list[Message]:
        return []

    def persist_run(self, run_id: str, session_id: str, user_message_id: str,
                    final_answer: Message, audit_records: list[Message],
                    metadata: dict, pre_messages: list | None = None) -> str:
        self._id_counter += 1
        mid = f"msg-{self._id_counter}"
        self.messages.append({
            "id": mid,
            "role": final_answer.role,
            "content": final_answer.content,
            "metadata": {**metadata, "run_id": run_id},
            "session_id": session_id,
        })
        return mid


class FakeLoop:
    """Minimal fake loop for testing RunService."""
    def __init__(self, model_id: str = "test:model"):
        self.model_id = model_id
        self.rubric = None
        self.cancel_event = None
        self._reset_state()

    def _reset_state(self) -> None:
        # Mirrors the real AgentLoop: a fresh AgentState per run with
        # messages + extra (middleware/verification state).
        self.state = type("State", (), {"messages": [], "extra": {}})()

    async def run(self, messages):
        self._reset_state()
        self.state.messages = list(messages) + [Message.assistant(content="Test response")]
        return self.state.messages

    async def run_stream(self, messages):
        self._reset_state()
        self.state.messages = list(messages) + [Message.assistant(content="Test response")]
        yield StreamChunk(type="text_delta", content="Test response")
        yield StreamChunk(type="done", content="Test response")

async def _no_rubric(*args: Any, **kwargs: Any) -> None:
    """Patched load_rubric_middleware: verification disabled."""
    return None


async def _register_rerun_handler() -> None:
    """Register the default rerun handler (as main.py does at startup)."""
    from src.sdk.loops.events import default_trigger_handler, get_trigger_registry

    get_trigger_registry().register("rerun", default_trigger_handler)



@pytest.mark.asyncio
async def test_execute_enters_trace_run_context(monkeypatch):
    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.middleware_rubric.load_rubric_middleware", _no_rubric)

    entered: list[tuple[str, str]] = []

    @contextmanager
    def fake_trace_run(user_id: str, session_id: str):
        entered.append((user_id, session_id))
        yield None

    monkeypatch.setattr("src.sdk.langfuse_tracer.LangfuseTracer.trace_run", fake_trace_run)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    result = await service.execute(
        session_id="chat-1",
        prompt="Hello",
    )

    assert result.run_id is not None
    assert entered == [("test-user", "chat-1")]


@pytest.mark.asyncio
async def test_execute_stream_enters_trace_run_context(monkeypatch):
    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.middleware_rubric.load_rubric_middleware", _no_rubric)

    entered: list[tuple[str, str]] = []

    @contextmanager
    def fake_trace_run(user_id: str, session_id: str):
        entered.append((user_id, session_id))
        yield None

    monkeypatch.setattr("src.sdk.langfuse_tracer.LangfuseTracer.trace_run", fake_trace_run)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    events = [
        event
        async for event in service.execute_stream(
            session_id="chat-1",
            prompt="Hello",
        )
    ]

    assert len(events) > 0
    assert entered == [("test-user", "chat-1")]


@pytest.mark.asyncio
async def test_load_rubric_middleware_does_not_double_wrap_grader_provider(monkeypatch):
    class FakeProvider:
        pass

    def fake_create_model_from_config(model, user_id=None):
        return FakeProvider()

    settings = SimpleNamespace(verification=SimpleNamespace(
        enabled=True,
        grader_model="test:grader",
        max_iterations=3,
        default_rubric=None,
    ))
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        "src.sdk.providers.factory.create_model_from_config", fake_create_model_from_config
    )

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    mw = await service._load_rubric_middleware(FakeLoop(), rubric="test rubric")

    assert mw is not None
    # The factory (create_model_from_config) owns provider wrapping —
    # _load_rubric_middleware must pass the provider through untouched so
    # the grader's LLM calls are not double-traced.
    assert mw._grader_provider is not None
    assert not hasattr(mw._grader_provider, "_original_chat")


@pytest.mark.asyncio
async def test_load_rubric_middleware_skips_wrap_when_langfuse_disabled(monkeypatch):
    class FakeProvider:
        pass

    def fake_create_model_from_config(model, user_id=None):
        return FakeProvider()

    settings = SimpleNamespace(verification=SimpleNamespace(
        enabled=True,
        grader_model="test:grader",
        max_iterations=3,
        default_rubric=None,
    ))
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        "src.sdk.providers.factory.create_model_from_config", fake_create_model_from_config
    )
    # Real wrap_provider is a no-op when Langfuse is disabled — the provider
    # passes through unchanged and the middleware still works.
    monkeypatch.setattr("src.sdk.langfuse_tracer.LangfuseTracer.is_enabled", lambda: False)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    mw = await service._load_rubric_middleware(FakeLoop(), rubric="test rubric")

    assert mw is not None
    assert isinstance(mw._grader_provider, FakeProvider)
    assert not hasattr(mw._grader_provider, "_original_chat")


@pytest.mark.asyncio
async def test_grader_failed_result_routes_to_invalid_rubric(monkeypatch):
    """A grader 'failed' verdict (malformed rubric) must not crash the run
    — it routes to the INVALID_RUBRIC terminal status."""
    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)

    class FakeGrader:
        max_iterations = 3
        grader_model_id = "test:grader"

        async def grade(self, messages, iteration):
            return {
                "grading_run_id": "grading-1",
                "iteration": iteration,
                "result": "failed",
                "explanation": "Rubric is malformed",
                "criteria": [],
            }

    async def _load_fake(*a: Any, **k: Any) -> Any:
        return FakeGrader()

    monkeypatch.setattr("src.sdk.middleware_rubric.load_rubric_middleware", _load_fake)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    result = await service.execute(
        session_id="chat-1",
        prompt="Hello",
    )

    assert result.verification.availability.value == "on"
    assert result.verification.status.value == "invalid_rubric"
    assert result.status.value == "completed"


@pytest.mark.asyncio
async def test_execute_stream_done_event_attempt_matches_final_rubric_attempt(monkeypatch):
    """A rubric-revised run must emit a done event whose envelope attempt
    matches the final attempt. Previously the envelope always carried
    attempt=1, failing DoneEvent validation ('result attempt must match
    envelope') and surfacing an ErrorEvent instead."""
    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)

    class FakeGrader:
        max_iterations = 3
        grader_model_id = "test:grader"

        async def grade(self, messages, iteration):
            result = "needs_revision" if iteration == 0 else "satisfied"
            return {
                "grading_run_id": f"grading-{iteration + 1}",
                "iteration": iteration,
                "result": result,
                "explanation": "revise once then pass",
                "criteria": [
                    {"name": "c", "passed": result == "satisfied", "gap": "revise" if result != "satisfied" else None}
                ],
            }

    async def _load_fake(*a: Any, **k: Any) -> Any:
        return FakeGrader()

    monkeypatch.setattr("src.sdk.middleware_rubric.load_rubric_middleware", _load_fake)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)
    await _register_rerun_handler()

    events = [e async for e in service.execute_stream(session_id="chat-1", prompt="Hello")]
    done = [e for e in events if e.type == "done"]
    assert len(done) == 1, f"expected a done event, got types: {[e.type for e in events]}"
    assert done[0].attempt == 2
    assert done[0].data.result.attempt == 2
    assert done[0].data.result.status.value == "completed"


@pytest.mark.asyncio
async def test_failed_run_after_evaluation_reports_cancelled_not_satisfied(monkeypatch):
    """When the agent dies on a later revision attempt after earlier
    evaluations, the verification must not claim satisfaction (previously
    the NOT_RUN -> SATISFIED fallback produced a satisfied status whose
    latest evaluation was needs_revision, which the outcome validator
    rejects and which is semantically wrong)."""
    class FailingLoop(FakeLoop):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def run(self, messages):
            self.calls += 1
            if self.calls == 1:
                return [Message.assistant(content="first attempt")]
            raise RuntimeError("agent died on revision attempt")

    async def fake_get_sdk_loop(*args, **kwargs):
        return FailingLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)

    class FakeGrader:
        max_iterations = 3
        grader_model_id = "test:grader"

        async def grade(self, messages, iteration):
            return {
                "grading_run_id": f"grading-{iteration + 1}",
                "iteration": iteration,
                "result": "needs_revision",
                "explanation": "keep revising",
                "criteria": [{"name": "c", "passed": False, "gap": "x"}],
            }

    async def _load_fake(*a: Any, **k: Any) -> Any:
        return FakeGrader()

    monkeypatch.setattr("src.sdk.middleware_rubric.load_rubric_middleware", _load_fake)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)
    await _register_rerun_handler()

    result = await service.execute(session_id="chat-1", prompt="Hello")

    assert result.status.value == "failed"
    assert result.verification.status.value == "cancelled"


@pytest.mark.asyncio
async def test_run_service_execute_returns_run_result(monkeypatch):
    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.middleware_rubric.load_rubric_middleware", _no_rubric)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    result = await service.execute(
        session_id="chat-1",
        prompt="Hello",
    )

    assert result.run_id is not None
    assert result.session_id == "chat-1"
    assert result.status.value == "completed"
    assert result.response is not None


@pytest.mark.asyncio
async def test_run_service_execute_stream_yields_run_events(monkeypatch):
    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.middleware_rubric.load_rubric_middleware", _no_rubric)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    events = []
    async for event in service.execute_stream(
        session_id="chat-1",
        prompt="Hello",
    ):
        events.append(event)

    assert len(events) > 0
    assert events[-1].type == "done"
    assert events[0].sequence == 1
    for i, event in enumerate(events):
        assert event.sequence == i + 1
        assert event.run_id is not None
        assert event.session_id == "chat-1"
        assert event.attempt >= 1


@pytest.mark.asyncio
async def test_run_service_session_busy(monkeypatch):
    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    _lock = await registry.acquire("chat-1")
    try:
        with pytest.raises(SessionBusyError):
            await service.execute(session_id="chat-1", prompt="Hello")
    finally:
        await registry.release("chat-1")


@pytest.mark.asyncio
async def test_run_service_different_sessions_concurrent(monkeypatch):
    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()
    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr("src.sdk.run_service.register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.run_service.unregister_user_loop", lambda *a, **k: None)
    monkeypatch.setattr("src.sdk.middleware_rubric.load_rubric_middleware", _no_rubric)

    registry = SessionWorkerRegistry()
    store = InMemoryMessageStore()
    service = RunService("test-user", registry, store)

    import asyncio
    results = await asyncio.gather(
        service.execute(session_id="chat-1", prompt="Hello"),
        service.execute(session_id="chat-2", prompt="World"),
    )
    assert len(results) == 2
    assert results[0].session_id == "chat-1"
    assert results[1].session_id == "chat-2"
