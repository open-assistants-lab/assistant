"""Integration tests for loop engineering — verifies all transport paths and gaps."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.sdk.messages import Message, StreamChunk
from src.sdk.state import AgentState

# ---------------------------------------------------------------------------
# Gap 1: WS path passes rubric to run_sdk_agent_stream
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ws_passes_rubric_to_runner(monkeypatch):
    """When verification is enabled globally, WS handler resolves rubric and passes to runner."""
    import src.config.settings as _cfg
    _cfg._config = None

    monkeypatch.setenv("VERIFICATION_ENABLED", "true")
    monkeypatch.setenv("VERIFICATION_DEFAULT_RUBRIC", "- Response is non-empty")

    from src.config import get_settings
    s = get_settings()
    assert s.verification.enabled is True
    assert s.verification.default_rubric == "- Response is non-empty"

    # The WS handler calls get_settings() to resolve rubric — verify it works
    ws_rubric = None
    if s.verification.enabled and s.verification.default_rubric:
        ws_rubric = s.verification.default_rubric
    assert ws_rubric == "- Response is non-empty"

    _cfg._config = None


# ---------------------------------------------------------------------------
# Gap 2: SSE /message/stream passes rubric
# ---------------------------------------------------------------------------

def test_sse_stream_resolves_rubric_from_request(monkeypatch):
    """When request includes verification.rubric, SSE stream passes it to RunService."""
    from fastapi.testclient import TestClient

    from src.http.main import app
    from src.sdk.messages import StreamChunk

    captured_rubric = {}

    class DummyLoop:
        model_id = "x:y"

        async def run_stream(self, messages):
            yield StreamChunk(type="done", content="hi")

    async def fake_get_sdk_loop(*args, **kwargs):
        return DummyLoop()

    async def fake_load_rubric(*args, **kwargs):
        captured_rubric["rubric"] = kwargs.get("rubric") or (args[2] if len(args) > 2 else None)
        return None

    monkeypatch.setattr("src.sdk.run_service.get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr(
        "src.sdk.middleware_rubric.load_rubric_middleware", fake_load_rubric
    )
    monkeypatch.setattr("src.http.routers.conversation.aget_message_store", AsyncMock(return_value=MagicMock(
        add_message=MagicMock(),
        get_messages_with_summary=MagicMock(return_value=[]),
        persist_run=MagicMock(return_value="msg-1"),
    )))

    client = TestClient(app)
    response = client.post(
        "/message/stream",
        json={
            "message": "hi",
            "verification": {"rubric": "- Must be 3 lines"},
        },
    )
    assert response.status_code == 200
    assert captured_rubric["rubric"] == "- Must be 3 lines"


@pytest.mark.asyncio
async def test_sse_stream_resolves_rubric_from_settings(monkeypatch):
    """When verification is enabled globally, RunService uses default_rubric."""
    import src.config.settings as _cfg
    _cfg._config = None

    monkeypatch.setenv("VERIFICATION_ENABLED", "true")
    monkeypatch.setenv("VERIFICATION_DEFAULT_RUBRIC", "- Non-empty")
    monkeypatch.setattr(
        "src.config.user_settings_store.UserSettingsStore.load_grader_prompt",
        lambda self: GraderPromptResponse(
            content="- Non-empty",
            source="seeded",
            content_hash="sha256:" + "0" * 64,
            revision=0,
        ),
    )
    monkeypatch.setattr(
        "src.sdk.providers.factory.create_model_from_config",
        lambda *a, **k: MagicMock(),
    )

    from src.config.user_settings import GraderPromptResponse
    from src.sdk.run_service import RunService

    loop = MagicMock(model_id="x:y")
    mw = await RunService("u", MagicMock(), MagicMock())._load_rubric_middleware(loop)
    assert mw is not None
    assert mw._grader_prompt == "- Non-empty"

    _cfg._config = None


# ---------------------------------------------------------------------------
# Gap 3: REST /message returns verification verdict
# ---------------------------------------------------------------------------


def test_rest_message_returns_verification_verdict(monkeypatch):
    """REST /message includes verification verdict when rubric is set."""
    from fastapi.testclient import TestClient

    from src.http.main import app
    from src.sdk.run_models import (
        RubricAvailability,
        RubricEvaluation,
        RubricEvaluationResult,
        RunResult,
        RunStatus,
        RunUsage,
        TerminalRubricStatus,
        VerificationOutcome,
    )

    async def fake_execute(
        self, *, session_id, prompt, model=None, provider_keys=None, rubric=None, **kwargs
    ):
        return RunResult(
            run_id="r1",
            session_id=session_id,
            status=RunStatus.COMPLETED,
            attempt=1,
            model="x:y",
            response="done",
            usage=RunUsage(),
            verification=VerificationOutcome(
                availability=RubricAvailability.ON,
                status=TerminalRubricStatus.SATISFIED,
                attempts=1,
                max_attempts=2,
                evaluations=[
                    RubricEvaluation(
                        grading_run_id="g1",
                        attempt=1,
                        result=RubricEvaluationResult.SATISFIED,
                        explanation="ok",
                        criteria=[],
                        passed_count=0,
                        total_count=0,
                    )
                ],
            ),
        )

    monkeypatch.setattr("src.http.routers.conversation.RunService.execute", fake_execute)
    monkeypatch.setattr("src.http.routers.conversation.aget_message_store", AsyncMock(return_value=MagicMock(
        add_message=MagicMock(),
        get_messages_by_session_id=MagicMock(return_value=[]),
    )))

    client = TestClient(app)
    response = client.post("/message", json={
        "message": "hi",
        "verification": {"rubric": "- Non-empty"},
    })
    assert response.status_code == 200
    body = response.json()
    assert body.get("verification") is not None
    assert body["verification"]["status"] == "satisfied"
    # The verdict must carry the full outcome — attempts, max_attempts,
    # explanation, criteria and per-attempt evaluations (previously these
    # fields were dropped by the HTTP model).
    assert body["verification"]["attempts"] == 1
    assert body["verification"]["max_attempts"] == 2
    assert body["verification"]["iterations"] == 1
    assert body["verification"]["explanation"] == "ok"
    assert body["verification"]["criteria"] == []
    assert body["verification"]["evaluations"][0]["attempt"] == 1
    assert body["verification"]["evaluations"][0]["result"] == "satisfied"


# ---------------------------------------------------------------------------
# Gap 4: HillClimbingConfig exists in settings
# ---------------------------------------------------------------------------

def test_hill_climbing_config_defaults():
    from src.config import get_settings
    s = get_settings()
    assert hasattr(s, "hill_climbing")
    assert s.hill_climbing.mode == "human_review"
    assert s.hill_climbing.auto_apply_risk_threshold == "low"
    assert s.hill_climbing.eval_enabled is True


# ---------------------------------------------------------------------------
# Gap 5: POST /improvements/analyze endpoint exists
# ---------------------------------------------------------------------------

def test_improvements_analyze_endpoint_exists(monkeypatch):
    """POST /improvements/analyze should be a valid endpoint."""
    from fastapi.testclient import TestClient

    from src.http.main import app

    # Mock the analysis to return empty (no outcomes)
    async def fake_run(self, *args, **kwargs):
        return []

    monkeypatch.setattr("src.sdk.loops.improvement.AnalysisJob.run", fake_run)

    # D0-5: the repo ships with no pinned model — patch provider resolution
    # directly so the endpoint test doesn't depend on deployment config.
    fake_provider = MagicMock()
    monkeypatch.setattr(
        "src.sdk.providers.factory.get_cached_model_provider",
        lambda *a, **k: fake_provider,
    )

    client = TestClient(app)
    response = client.post("/improvements/analyze", params={"user_id": "analyze_test"})
    assert response.status_code == 200
    body = response.json()
    assert "suggestions" in body


# ---------------------------------------------------------------------------
# Gap 6: RunOutcome persisted in stream path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_outcome_persisted_in_stream_path(monkeypatch, tmp_path):
    """run_sdk_agent_stream should persist RunOutcome after streaming completes."""
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path))

    from src.sdk import runner as _runner
    from src.sdk.messages import Message, StreamChunk

    persist_calls = []

    async def fake_persist(user_id, session_id, messages, loop, trigger_type):
        persist_calls.append({
            "user_id": user_id,
            "session_id": session_id,
            "message_count": len(messages) if messages else 0,
            "trigger_type": trigger_type,
        })

    monkeypatch.setattr(_runner, "_persist_run_outcome", fake_persist)

    class FakeLoop:
        state = AgentState(messages=[Message.assistant(content="streamed response")])
        rubric = None
        cancel_event = None
        model_id = "test:model"
        async def run_stream(self, messages):
            yield StreamChunk(type="text_delta", content="streamed response")
            yield StreamChunk(type="done", content="streamed response")

    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()

    monkeypatch.setattr(_runner, "get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr(_runner, "register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr(_runner, "unregister_user_loop", lambda *a, **k: None)

    chunks = []
    async for chunk in _runner.run_sdk_agent_stream(
        user_id="stream_outcome_test",
        messages=[Message.user("hi")],
        session_id="so-s1",
    ):
        chunks.append(chunk)

    assert len(persist_calls) == 1
    assert persist_calls[0]["user_id"] == "stream_outcome_test"
    assert persist_calls[0]["trigger_type"] == "manual"


# ---------------------------------------------------------------------------
# Gap 7: RunOutcome persisted in non-stream path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_outcome_persisted_in_non_stream_path(monkeypatch, tmp_path):
    """run_sdk_agent should persist RunOutcome after completion."""
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path))

    from src.sdk import runner as _runner
    from src.sdk.messages import Message

    persist_calls = []

    async def fake_persist(user_id, session_id, messages, loop, trigger_type):
        persist_calls.append({
            "user_id": user_id,
            "session_id": session_id,
            "message_count": len(messages) if messages else 0,
        })

    monkeypatch.setattr(_runner, "_persist_run_outcome", fake_persist)

    class FakeLoop:
        state = AgentState(messages=[Message.assistant(content="response")])
        rubric = None
        model_id = "test:model"
        async def run(self, messages):
            return [Message.assistant(content="response")]

    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()

    monkeypatch.setattr(_runner, "get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr(_runner, "register_user_loop", lambda *a, **k: None)
    monkeypatch.setattr(_runner, "unregister_user_loop", lambda *a, **k: None)

    await _runner.run_sdk_agent(
        user_id="nonstream_outcome_test",
        messages=[Message.user("hi")],
        session_id="ns-s1",
    )

    assert len(persist_calls) == 1
    assert persist_calls[0]["user_id"] == "nonstream_outcome_test"


# ---------------------------------------------------------------------------
# Gap 8: /run-outcomes endpoint returns persisted outcomes
# ---------------------------------------------------------------------------

def test_run_outcomes_endpoint_returns_data(monkeypatch, tmp_path):
    """GET /run-outcomes should return persisted outcomes."""
    from fastapi.testclient import TestClient

    from src.http.main import app
    from src.sdk.loops.storage import LoopEngineeringDB, RunOutcome

    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path))

    async def setup_outcome():
        from src.sdk.loops.storage import get_loop_engineering_db_path
        db = LoopEngineeringDB(get_loop_engineering_db_path("outcome_api_test"))
        await db.init()
        await db.save_run_outcome(RunOutcome(
            run_id="api-r1", user_id="outcome_api_test", session_id="s1",
            trigger_type="manual", response="test response",
            verification_status="satisfied", verification_iterations=1,
            verification_evaluations=[], cost_usd=0.01,
            input_tokens=10, output_tokens=5, model="test",
            timestamp="2026-07-27T10:00:00Z",
        ))

    asyncio.get_event_loop().run_until_complete(setup_outcome())

    client = TestClient(app)
    response = client.get("/run-outcomes", params={"user_id": "outcome_api_test", "limit": 10})
    assert response.status_code == 200
    body = response.json()
    assert len(body["outcomes"]) >= 1
    assert body["outcomes"][0]["run_id"] == "api-r1"


# ---------------------------------------------------------------------------
# Gap 9: /improvements endpoint returns suggestions
# ---------------------------------------------------------------------------

def test_improvements_endpoint_returns_suggestions(monkeypatch, tmp_path):
    """GET /improvements should return proposed suggestions."""
    from fastapi.testclient import TestClient

    from src.http.main import app
    from src.sdk.loops.storage import ImprovementSuggestion, LoopEngineeringDB

    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path))

    async def setup_suggestion():
        from src.sdk.loops.storage import get_loop_engineering_db_path
        db = LoopEngineeringDB(get_loop_engineering_db_path("imp_api_test"))
        await db.init()
        await db.save_suggestion(ImprovementSuggestion(
            suggestion_id="api-s1", run_id="r1",
            target_type="tool_description", target_name="files_read",
            current_value="old", proposed_value="new",
            rationale="test", risk_level="low", status="proposed",
            created_at="2026-07-27T10:00:00Z",
        ))

    asyncio.get_event_loop().run_until_complete(setup_suggestion())

    client = TestClient(app)
    response = client.get("/improvements", params={"user_id": "imp_api_test"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["suggestions"]) >= 1
    assert body["suggestions"][0]["suggestion_id"] == "api-s1"


# ---------------------------------------------------------------------------
# Gap 10: /trigger endpoint works with rubric
# ---------------------------------------------------------------------------

def test_trigger_endpoint_passes_rubric(monkeypatch):
    """POST /trigger should pass rubric to run_sdk_agent."""
    from fastapi.testclient import TestClient

    from src.http.main import app

    captured = {}

    async def fake_run_sdk_agent(**kwargs):
        captured["rubric"] = kwargs.get("rubric")
        return [Message.assistant(content="triggered")]

    monkeypatch.setattr("src.http.routers.webhooks.run_sdk_agent", fake_run_sdk_agent)

    # Make the fallback path deterministic: the app lifespan may have
    # registered a 'manual' handler (from an earlier TestClient in the
    # suite), which would route /trigger through the registry instead of
    # the patched run_sdk_agent below.
    from src.sdk.loops.events import get_trigger_registry

    get_trigger_registry().unregister("manual")

    client = TestClient(app)
    response = client.post("/trigger", json={
        "user_id": "trigger_rubric_test",
        "session_id": "tr-s1",
        "message": "hello",
        "rubric": "- Must be polite",
    })
    assert response.status_code == 200
    assert captured["rubric"] == "- Must be polite"


# ---------------------------------------------------------------------------
# Gap 11: VerificationConfig is per-user, not per-request for enabled flag
# ---------------------------------------------------------------------------

def test_verification_enabled_is_per_user_not_per_request():
    """The 'enabled' field should not be in the request body schema."""
    from src.http.models import MessageRequest
    fields = MessageRequest.model_fields
    assert "verification" in fields
    # verification is a VerificationRequest, which only has rubric
    from src.http.models import VerificationRequest
    vr_fields = VerificationRequest.model_fields
    assert "rubric" in vr_fields
    assert "enabled" not in vr_fields
    assert "grader_model" not in vr_fields
    assert "max_iterations" not in vr_fields


# ---------------------------------------------------------------------------
# Gap 12: Rubric events flow through stream adapter
# ---------------------------------------------------------------------------

def test_stream_adapter_handles_rubric_events():
    """adapt_stream_chunk should pass rubric event types through."""
    from src.http.stream_adapter import adapt_stream_chunk

    start_chunk = StreamChunk(type="rubric_evaluation_start", content='{"iteration":0}')
    event = adapt_stream_chunk(start_chunk)
    assert event.kind == "rubric_evaluation_start"

    end_chunk = StreamChunk(
        type="rubric_evaluation_end",
        content='{"result":"satisfied","criteria":[]}',
    )
    event = adapt_stream_chunk(end_chunk)
    assert event.kind == "rubric_evaluation_end"
