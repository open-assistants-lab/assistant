"""Issue #17 regression: streaming observations nest under the run trace root.

The SSE consumer drives each generator step in a NEW asyncio task, so a plain
`async for` inside trace_run loses the trace context on the next resume. The
pump-task pattern binds the whole body to the run context at creation.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars

import pytest

from src.sdk.langfuse_tracer import LangfuseTracer

marker = contextvars.ContextVar("lf_marker", default=None)
opened: list[tuple[str, str | None]] = []


class _StubTrace:
    def __init__(self, is_root: bool = False):
        self._is_root = is_root

    def __enter__(self):
        opened.append(("open", marker.get()))
        # Only the RUN root claims the context — a child's own enter must
        # not set the marker (that would mask context loss).
        if self._is_root:
            marker.set("run")
        return self

    def __exit__(self, *a):
        opened.append(("close", marker.get()))
        return False

    def update(self, *a, **k):
        pass


class _StubClient:
    def start_as_current_observation(self, *, as_type, name):
        opened.append(("obs", marker.get()))
        return _StubTrace(is_root=(name == "run"))


@pytest.fixture()
def stub_tracer(monkeypatch):
    opened.clear()
    monkeypatch.setattr(
        LangfuseTracer, "ensure_initialized", classmethod(lambda cls: None)
    )
    monkeypatch.setattr(
        LangfuseTracer, "_get_client", classmethod(lambda cls: _StubClient())
    )
    import langfuse as lf_mod

    monkeypatch.setattr(
        lf_mod, "propagate_attributes", lambda **k: contextlib.nullcontext()
    )


def _svc(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from src.sdk.run_service import RunService

    svc = RunService(user_id="lf_user", registry=MagicMock(), message_store=MagicMock())
    svc._registry.acquire = AsyncMock()
    svc._registry.release = AsyncMock()

    async def _fake_stream(*a, **k):
        # Opens a child observation the way real body code does — via the
        # tracer client. The SSE consumer resumes this generator in a NEW
        # task per step (ensure_future in conversation.py) — the trace
        # contextvar must survive that boundary.
        yield {"type": "text_start"}
        # Cross the per-step task boundary BEFORE the child observation.
        with client_stub.start_as_current_observation(
            as_type="generation", name="child"
        ):
            opened.append(("child-obs", marker.get()))
        yield {"type": "text_delta", "content": "hi"}
        yield {"type": "text_end"}

    monkeypatch.setattr(svc, "_run_stream", _fake_stream)
    return svc


client_stub = _StubClient()


def test_stream_observations_nest_under_run_root(stub_tracer, monkeypatch):
    svc = _svc(monkeypatch)

    # Reproduce the SSE consumer: ensure_future per step (each task copies
    # the CALLER's context, not the run context).
    async def consume():
        it = svc.execute_stream("s1", "hi")
        events = []
        task = asyncio.ensure_future(it.__anext__())
        while True:
            try:
                events.append(await task)
            except StopAsyncIteration:
                break
            task = asyncio.ensure_future(it.__anext__())
        return events

    events = asyncio.run(consume())

    assert len(events) == 3
    # the child observation opened INSIDE the run context: marker == "run"
    child = [e for e in opened if e[0] == "child-obs"]
    assert child and child[0][1] == "run", f"orphan root trace: {opened}"
    assert all(v == "run" for k, v in opened if k == "child-obs")
