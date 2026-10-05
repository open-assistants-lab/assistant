"""B4: WS/SSE transport fidelity (#140, #141, #142, #143, #144, #145)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect
from starlette.testclient import TestClient

from src.http.routers import ws as ws_router
from src.sdk.messages import StreamChunk
from src.sdk.run_events import (
    BlockDeltaData,
    DoneData,
    DoneEvent,
    RevisionStartData,
    RevisionStartEvent,
    TextDeltaEvent,
    ToolDeltaData,
    ToolEndData,
    ToolInputDeltaEvent,
    ToolInputEndEvent,
    ToolInputStartEvent,
    ToolResultData,
    ToolResultEvent,
    ToolStartData,
)
from src.sdk.run_models import RunResult, RunStatus, RunUsage, VerificationOutcome
from tests.api.conftest import make_run_event_factory


class FakeWebSocket:
    client = None

    def __init__(self, messages, release=None):
        self.messages = list(messages)
        self.sent = []
        self._release = release

    async def accept(self):
        pass

    async def receive_text(self):
        if self.messages:
            return self.messages.pop(0)
        if self._release is not None:
            await self._release.wait()
        raise WebSocketDisconnect()

    async def send_json(self, payload):
        self.sent.append(payload)


class FakeConversation:
    def __init__(self, history=None):
        self.rows: list[tuple[str, str]] = []
        self.history = list(history or [])
        self._calls = 0

    def add_message(self, role, content, metadata=None, session_id=None, **kwargs):
        self.rows.append((role, str(content)))
        return f"msg-{len(self.rows)}"

    def get_messages_with_summary(self, *, session_id, limit):
        self._calls += 1
        return list(self.history)


def _settings(monkeypatch):
    monkeypatch.setattr(
        ws_router,
        "get_settings",
        lambda: SimpleNamespace(auth=SimpleNamespace(api_key="", solo_bypass=True)),
    )


def _common(run_id="r1"):
    return dict(
        event_id="e1",
        sequence=1,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        session_id="default",
        run_id=run_id,
        attempt=1,
    )


def _done_event(response: str, run_id="r1"):
    return DoneEvent(
        data=DoneData(
            result=RunResult(
                run_id=run_id,
                session_id="default",
                status=RunStatus.COMPLETED,
                attempt=1,
                model="x:y",
                response=response,
                final_message_id="msg-1",
                usage=RunUsage(),
                verification=VerificationOutcome(),
                persisted_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ),
        **_common(run_id),
    )


class TestFollowUpSteerUsesItsOwnText:
    """#140: a text-only steer must become the follow-up prompt."""

    @pytest.mark.asyncio
    async def test_followup_prompt_is_the_steer(self, monkeypatch):
        _settings(monkeypatch)
        prompts_seen: list[str] = []
        release = asyncio.Event()

        class StubLoop:
            def __init__(self, pending):
                self._pending = list(pending)

            def has_pending_steer(self):
                return bool(self._pending)

            def pop_steer(self):
                return self._pending.pop(0) if self._pending else None

        async def chunk_gen(**kwargs):
            yield StreamChunk.text_delta(content="working")
            yield StreamChunk.done(content="working")

        base_fake = make_run_event_factory(chunk_gen)

        async def fake_execute_stream(service_self, **kwargs):
            prompts_seen.append(kwargs.get("prompt"))
            on_end = kwargs.pop("on_stream_end", None)
            events = [e async for e in base_fake(service_self, **kwargs)]
            if on_end is not None and len(prompts_seen) == 1:
                on_end(StubLoop(["focus on the tests"]))
            if release is not None and len(prompts_seen) >= 2:
                release.set()  # the follow-up run started: socket may close after it
            for e in events:
                yield e

        conversation = FakeConversation()
        monkeypatch.setattr(
            ws_router, "aget_message_store", AsyncMock(return_value=conversation)
        )
        monkeypatch.setattr(ws_router.RunService, "execute_stream", fake_execute_stream)

        websocket = FakeWebSocket(
            [json.dumps({"type": "user_message", "content": "go", "user_id": "t"})],
            release,
        )
        try:
            await asyncio.wait_for(ws_router.ws_conversation(websocket), timeout=8)
        except (TimeoutError, WebSocketDisconnect):
            pass

        assert prompts_seen, "no run happened"
        assert prompts_seen[0] == "go"
        assert "focus on the tests" in prompts_seen, (
            f"the steer never became a follow-up prompt: {prompts_seen}"
        )


class TestRunServiceGetsTheCurrentPrompt:
    """#140 companion: the follow-up run's prompt is not re-persisted twice."""

    @pytest.mark.asyncio
    async def test_deferred_steer_frame_becomes_a_followup_turn(self, monkeypatch):
        """A steer arriving with no active run becomes the next turn, not an error."""
        _settings(monkeypatch)
        prompts_seen: list[str] = []

        async def chunk_gen(**kwargs):
            yield StreamChunk.text_delta(content="ok")
            yield StreamChunk.done(content="ok")

        base_fake = make_run_event_factory(chunk_gen)

        async def fake_execute_stream(service_self, **kwargs):
            prompts_seen.append(kwargs.get("prompt"))
            if release is not None and len(prompts_seen) >= 2:
                release.set()
            async for e in base_fake(service_self, **kwargs):
                yield e

        conversation = FakeConversation()
        monkeypatch.setattr(
            ws_router, "aget_message_store", AsyncMock(return_value=conversation)
        )
        monkeypatch.setattr(ws_router.RunService, "execute_stream", fake_execute_stream)

        release = asyncio.Event()
        websocket = FakeWebSocket(
            [
                json.dumps({"type": "user_message", "content": "go", "user_id": "t"}),
                json.dumps({"type": "steer", "content": "change plan"}),
            ],
            release,
        )
        try:
            await asyncio.wait_for(ws_router.ws_conversation(websocket), timeout=5)
        except (TimeoutError, WebSocketDisconnect):
            pass

        assert "change plan" in prompts_seen, (
            f"a steer with no active run was dropped: {prompts_seen}"
        )
        acks = [m for m in websocket.sent if m.get("type") == "steer_ack"]
        assert acks, "the steer was not acknowledged"


class TestRevisionRestart:
    """#143: the final response is the LAST attempt, not the concatenation."""

    @pytest.mark.asyncio
    async def test_ws_done_carries_only_the_final_attempt(self, monkeypatch):
        _settings(monkeypatch)

        async def fake_execute_stream(service_self, **kwargs):
            yield TextDeltaEvent(data=BlockDeltaData(block_id="b", delta="Draft"), **_common())
            yield RevisionStartEvent(
                data=RevisionStartData(previous_attempt=1, new_attempt=2, max_attempts=3),
                sequence=2,
                event_id="e2",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session_id="default",
                run_id="r1",
                attempt=2,
            )
            yield TextDeltaEvent(
                data=BlockDeltaData(block_id="b2", delta="Correct"),
                sequence=3, event_id="e3", timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session_id="default", run_id="r1", attempt=2,
            )
            yield _done_event("Correct")

        conversation = FakeConversation()
        monkeypatch.setattr(
            ws_router, "aget_message_store", AsyncMock(return_value=conversation)
        )
        monkeypatch.setattr(ws_router.RunService, "execute_stream", fake_execute_stream)

        websocket = FakeWebSocket([
            json.dumps({"type": "user_message", "content": "go", "user_id": "t"}),
        ])
        try:
            await asyncio.wait_for(ws_router.ws_conversation(websocket), timeout=5)
        except (TimeoutError, WebSocketDisconnect):
            pass

        dones = [m for m in websocket.sent if m.get("type") == "done"]
        assert dones, ["no done frame"]
        assert dones[-1].get("response") == "Correct", (
            f"rejected draft concatenated into the answer: {dones[-1].get('response')!r}"
        )

    def test_sse_also_resets_on_revision(self):
        import inspect

        import src.http.routers.conversation as conv

        source = inspect.getsource(conv)
        revision_branch = source[
            source.index('event_type == "response_revision_start"') :
        ][:500]
        assert "ai_content_parts.clear()" in revision_branch, (
            "the sse revision branch does not reset the collected attempt text"
        )


class TestNonObjectFrames:
    """#144: a valid-JSON non-object frame must not kill the handler."""

    @pytest.mark.asyncio
    async def test_non_object_frame_gets_parse_error_and_handler_survives(self, monkeypatch):
        _settings(monkeypatch)

        async def chunk_gen(**kwargs):
            yield StreamChunk.text_delta(content="ok")
            yield StreamChunk.done(content="ok")

        base_fake = make_run_event_factory(chunk_gen)

        async def fake_execute_stream(service_self, **kwargs):
            async for e in base_fake(service_self, **kwargs):
                yield e

        conversation = FakeConversation()
        monkeypatch.setattr(
            ws_router, "aget_message_store", AsyncMock(return_value=conversation)
        )
        monkeypatch.setattr(ws_router.RunService, "execute_stream", fake_execute_stream)

        websocket = FakeWebSocket([
            "[]",                # valid JSON, not an object
            json.dumps({"type": "ping"}),
        ])
        try:
            await asyncio.wait_for(ws_router.ws_conversation(websocket), timeout=5)
        except (TimeoutError, WebSocketDisconnect):
            pass

        assert any(m.get("type") == "error" and m.get("code") == "PARSE_ERROR" for m in websocket.sent), (
            f"non-object frame not reported: {[m.get('type') for m in websocket.sent]}"
        )
        assert any(m.get("type") == "pong" for m in websocket.sent), (
            "the handler died after the non-object frame (no pong to a later ping)"
        )


class TestSkillsLoadNotification:
    """#145: the loaded skill's name comes from the tool END arguments."""

    @pytest.mark.asyncio
    async def test_notification_carries_the_skill_name(self, monkeypatch):
        _settings(monkeypatch)
        events = [
            ToolInputStartEvent(
                data=ToolStartData(block_id="b", tool_call_id="c1", name="skills_load"), **_common()
            ),
            ToolInputEndEvent(
                data=ToolEndData(block_id="b", tool_call_id="c1", arguments={"name": "python-patterns"}),
                sequence=2, event_id="e2", timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session_id="default", run_id="r1", attempt=1,
            ),
            ToolResultEvent(
                data=ToolResultData(
                    block_id="b", tool_call_id="c1", name="skills_load",
                    status="completed", content="loaded",
                ),
                sequence=3, event_id="e3", timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session_id="default", run_id="r1", attempt=1,
            ),
            _done_event("ok"),
        ]

        async def fake_execute_stream(service_self, **kwargs):
            for e in events:
                yield e

        conversation = FakeConversation()
        monkeypatch.setattr(
            ws_router, "aget_message_store", AsyncMock(return_value=conversation)
        )
        monkeypatch.setattr(ws_router.RunService, "execute_stream", fake_execute_stream)

        websocket = FakeWebSocket([
            json.dumps({"type": "user_message", "content": "go", "user_id": "t"}),
        ])
        try:
            await asyncio.wait_for(ws_router.ws_conversation(websocket), timeout=5)
        except (TimeoutError, WebSocketDisconnect):
            pass

        notifications = [m for m in websocket.sent if m.get("type") == "skills_load"]
        assert notifications, "no skills_load notification"
        assert notifications[-1]["data"]["name"] == "python-patterns", notifications[-1]


class TestSseForwardsBlockFrames:
    """#142: SSE must forward the canonical block/argument frames."""

    def _client(self, monkeypatch, events):
        from src.http.main import app

        async def fake_execute_stream(service_self, **kwargs):
            for e in events:
                yield e

        from src.sdk.run_service import RunService

        monkeypatch.setattr(RunService, "execute_stream", fake_execute_stream)
        return TestClient(app)

    def test_tool_argument_frames_reach_sse_clients(self, monkeypatch, tmp_path):
        events = [
            ToolInputStartEvent(
                data=ToolStartData(block_id="b", tool_call_id="c1", name="files_read"), **_common()
            ),
            ToolInputDeltaEvent(
                data=ToolDeltaData(block_id="b", tool_call_id="c1", delta='{"path": "x"}'),
                sequence=5, event_id="e5", timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session_id="default", run_id="r1", attempt=1,
            ),
            ToolInputEndEvent(
                data=ToolEndData(block_id="b", tool_call_id="c1", arguments={"path": "x"}),
                sequence=2, event_id="e2", timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session_id="default", run_id="r1", attempt=1,
            ),
            ToolResultEvent(
                data=ToolResultData(
                    block_id="b", tool_call_id="c1", name="files_read",
                    status="completed", content="ok",
                ),
                sequence=3, event_id="e3", timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session_id="default", run_id="r1", attempt=1,
            ),
            _done_event("ok"),
        ]
        client = self._client(monkeypatch, events)
        with client.stream(
            "POST", "/message/stream", json={"message": "go", "user_id": "sse_user"}
        ) as response:
            body = "".join(response.iter_text())
        assert "tool_input_delta" in body, "streamed tool args never reached the client"
        assert "tool_input_end" in body, "block end never reached the client"
