"""Shared fixtures for API contract tests."""

import os
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("USER_ID", "test_api_user")


@pytest.fixture(scope="session", autouse=True)
def isolated_data_path():
    """Keep API contract tests from reading or deleting local app data."""
    orig_data_path = os.environ.get("DEPLOYMENT_DATA_PATH")
    orig_ea_root = os.environ.get("DEPLOYMENT_EA_ROOT")
    with tempfile.TemporaryDirectory() as data_path:
        os.environ["DEPLOYMENT_DATA_PATH"] = data_path
        os.environ["DEPLOYMENT_EA_ROOT"] = str(Path(data_path) / "ea_root")

        from src.config import reload_settings
        from src.storage.messages import _stores
        from src.storage.paths import _paths_cache

        reload_settings()
        _stores.clear()
        _paths_cache.clear()
        yield data_path
        del os.environ["DEPLOYMENT_EA_ROOT"]
        del os.environ["DEPLOYMENT_DATA_PATH"]
        if orig_data_path is not None:
            os.environ["DEPLOYMENT_DATA_PATH"] = orig_data_path
        if orig_ea_root is not None:
            os.environ["DEPLOYMENT_EA_ROOT"] = orig_ea_root
        reload_settings()


@pytest.fixture(scope="session")
def app(isolated_data_path):
    """Create FastAPI app for testing."""
    from src.http.main import app

    return app


@pytest.fixture(scope="session")
def client(app):
    """Create a TestClient for the FastAPI app."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def test_user_id(request):
    """Return a unique test user ID."""
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", request.node.name)
    return f"test_api_{os.getpid()}_{safe_name}"


@pytest.fixture
def test_user_id_2(request):
    """Return a second test user ID for isolation tests."""
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", request.node.name)
    return f"test_api_2_{os.getpid()}_{safe_name}"


@pytest.fixture
def conversation_messages():
    """Sample messages for conversation tests."""
    return [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "What can you do?"},
        {"role": "assistant", "content": "I can help with many tasks."},
    ]


def make_run_event_factory(chunk_gen):
    """Build a fake `RunService.execute_stream` that replays StreamChunks as RunEvents.

    The routers now consume RunEvents from RunService.execute_stream (not StreamChunks
    from run_sdk_agent_stream). Legacy tests written against StreamChunk generators are
    bridged here: each chunk is normalized via adapt_stream_chunk and emitted as the
    matching canonical RunEvent, preserving ordering and the cancel/error semantics the
    tests rely on. `done` chunks carry the final response (RunResult.response) so the
    routers' no-text-delta fallback still works.
    """
    from datetime import UTC, datetime

    from src.http.stream_adapter import adapt_stream_chunk
    from src.sdk.run_events import (
        BlockDeltaData,
        DoneData,
        DoneEvent,
        ErrorData,
        ErrorEvent,
        InterruptData,
        InterruptEvent,
        ReasoningDeltaEvent,
        TextDeltaEvent,
        ToolInputStartEvent,
        ToolResultData,
        ToolResultEvent,
        ToolStartData,
    )
    from src.sdk.run_models import RunResult, RunStatus, RunUsage, VerificationOutcome

    _common = dict(
        event_id="e1",
        sequence=1,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        session_id="default",
        run_id="r1",
        attempt=1,
    )

    async def fake_execute_stream(
        self, *, session_id=None, prompt=None, model=None, provider_keys=None, **kwargs
    ):
        async for chunk in chunk_gen(
            session_id=session_id, prompt=prompt, model=model, provider_keys=provider_keys
        ):
            ev = adapt_stream_chunk(chunk)
            kind = ev.kind
            if kind == "text_delta" and ev.content:
                yield TextDeltaEvent(
                    data=BlockDeltaData(block_id="b", delta=ev.content), **_common
                )
            elif kind == "reasoning_delta" and ev.content:
                yield ReasoningDeltaEvent(
                    data=BlockDeltaData(block_id="b", delta=ev.content), **_common
                )
            elif kind == "tool_input_start" and ev.tool:
                yield ToolInputStartEvent(
                    data=ToolStartData(
                        block_id="b", tool_call_id=ev.call_id or "c", name=ev.tool
                    ),
                    **_common,
                )
            elif kind == "tool_result" and ev.tool:
                content = ev.result_preview or ev.content or ""
                yield ToolResultEvent(
                    data=ToolResultData(
                        block_id="b",
                        tool_call_id=ev.call_id or "c",
                        name=ev.tool,
                        status="failed" if ev.is_error else "completed",
                        content=content,
                    ),
                    **_common,
                )
            elif kind == "interrupt":
                yield InterruptEvent(
                    data=InterruptData(
                        tool=ev.tool or "t", call_id=ev.call_id or "c", args=ev.args or {}
                    ),
                    **_common,
                )
            elif kind == "error":
                yield ErrorEvent(
                    data=ErrorData(code="error", message=ev.content or "error", retryable=False),
                    **_common,
                )
            elif kind == "done":
                yield DoneEvent(
                    data=DoneData(
                        result=RunResult(
                            run_id="r1",
                            session_id="default",
                            status=RunStatus.COMPLETED,
                            attempt=1,
                            model="x:y",
                            response=ev.content or "",
                            final_message_id="msg-1",
                            usage=RunUsage(),
                            verification=VerificationOutcome(),
                            persisted_at=datetime(2026, 1, 1, tzinfo=UTC),
                        )
                    ),
                    **_common,
                )
            # Other kinds (text_start, reasoning_start, tool_end, etc.) are dropped,
            # mirroring the routers which only forward the canonical content events
            # these tests assert on.

    return fake_execute_stream
