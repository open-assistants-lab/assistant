"""Unit tests for RubricMiddleware."""

import json

import pytest

from src.sdk.messages import Message
from src.sdk.middleware_rubric import (
    RUBRIC_GRADER_SOURCE,
    GraderResponse,
    RubricMiddleware,
)
from src.sdk.state import AgentState


class FakeGraderProvider:
    def __init__(self, response_json: str):
        self._response_json = response_json
        self.call_count = 0

    async def chat(self, messages, tools=None, model=None, provider_options=None, **kwargs):
        self.call_count += 1
        return Message.assistant(content=self._response_json)

    async def chat_stream(self, *args, **kwargs):
        raise NotImplementedError

    async def list_models(self):
        return []


def _make_state(rubric: str | None = None, messages: list[Message] | None = None) -> AgentState:
    state = AgentState(messages=messages or [Message.user("write a haiku")])
    if rubric:
        state.extra["rubric"] = rubric
    return state


@pytest.mark.asyncio
async def test_no_rubric_is_noop():
    provider = FakeGraderProvider('{"result":"satisfied","explanation":"ok","criteria":[]}')
    mw = RubricMiddleware(grader_provider=provider)
    state = _make_state(rubric=None)
    await mw.aafter_agent(state)
    assert provider.call_count == 0
    assert not state.extra.get("_needs_rerun")


@pytest.mark.asyncio
async def test_satisfied_does_not_rerun():
    provider = FakeGraderProvider(json.dumps({
        "result": "satisfied", "explanation": "all good",
        "criteria": [{"name": "three lines", "passed": True}]
    }))
    mw = RubricMiddleware(grader_provider=provider)
    state = _make_state(rubric="- Three lines")
    await mw.aafter_agent(state)
    assert provider.call_count == 1
    assert not state.extra.get("_needs_rerun")
    assert state.extra["_rubric_status"] == "satisfied"


@pytest.mark.asyncio
async def test_needs_revision_injects_feedback_and_sets_rerun():
    provider = FakeGraderProvider(json.dumps({
        "result": "needs_revision", "explanation": "not enough lines",
        "criteria": [{"name": "three lines", "passed": False, "gap": "only two lines"}]
    }))
    mw = RubricMiddleware(grader_provider=provider)
    state = _make_state(rubric="- Three lines")
    await mw.aafter_agent(state)
    assert provider.call_count == 1
    assert state.extra.get("_needs_rerun") is True
    assert state.extra["_rubric_status"] == "needs_revision"
    feedback_msgs = [m for m in state.messages if getattr(m, "source", None) == RUBRIC_GRADER_SOURCE]
    assert len(feedback_msgs) == 1
    assert "three lines" in feedback_msgs[0].content.lower()


@pytest.mark.asyncio
async def test_max_iterations_reached():
    provider = FakeGraderProvider(json.dumps({
        "result": "needs_revision", "explanation": "still wrong",
        "criteria": [{"name": "three lines", "passed": False, "gap": "still wrong"}]
    }))
    mw = RubricMiddleware(grader_provider=provider, max_iterations=1)
    state = _make_state(rubric="- Three lines")
    state.extra["_rubric_iterations"] = 0
    await mw.aafter_agent(state)
    assert state.extra["_rubric_status"] == "max_iterations_reached"
    assert not state.extra.get("_needs_rerun")


@pytest.mark.asyncio
async def test_grader_error_on_malformed_json():
    provider = FakeGraderProvider("this is not json")
    mw = RubricMiddleware(grader_provider=provider)
    state = _make_state(rubric="- Three lines")
    await mw.aafter_agent(state)
    assert state.extra["_rubric_status"] == "grader_error"
    assert not state.extra.get("_needs_rerun")


@pytest.mark.asyncio
async def test_failed_verdict_no_rerun():
    provider = FakeGraderProvider(json.dumps({
        "result": "failed", "explanation": "rubric is contradictory", "criteria": []
    }))
    mw = RubricMiddleware(grader_provider=provider)
    state = _make_state(rubric="- Must be empty AND non-empty")
    await mw.aafter_agent(state)
    assert state.extra["_rubric_status"] == "failed"
    assert not state.extra.get("_needs_rerun")


@pytest.mark.asyncio
async def test_pending_stream_events_appended():
    provider = FakeGraderProvider(json.dumps({
        "result": "satisfied", "explanation": "ok",
        "criteria": [{"name": "lines", "passed": True}]
    }))
    mw = RubricMiddleware(grader_provider=provider)
    state = _make_state(rubric="- Three lines")
    await mw.aafter_agent(state)
    events = state.extra.get("_pending_stream_events", [])
    types = [e.type for e in events]
    assert "rubric_evaluation_start" in types
    assert "rubric_evaluation_end" in types


@pytest.mark.asyncio
async def test_on_evaluation_callback_fires():
    provider = FakeGraderProvider(json.dumps({
        "result": "satisfied", "explanation": "ok", "criteria": []
    }))
    evaluations = []
    mw = RubricMiddleware(grader_provider=provider, on_evaluation=evaluations.append)
    state = _make_state(rubric="- Three lines")
    await mw.aafter_agent(state)
    assert len(evaluations) == 1
    assert evaluations[0]["result"] == "satisfied"


def test_grader_response_consistency_validator():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        GraderResponse.model_validate({
            "result": "satisfied", "explanation": "ok",
            "criteria": [{"name": "x", "passed": False, "gap": "fail"}],
        })
    with pytest.raises(ValidationError):
        GraderResponse.model_validate({
            "result": "needs_revision", "explanation": "ok",
            "criteria": [{"name": "x", "passed": True}],
        })


@pytest.mark.asyncio
async def test_rubric_sends_score_to_langfuse_when_enabled(monkeypatch):
    from src.sdk.langfuse_tracer import LangfuseTracer

    score_calls = []

    def fake_score_current_trace(name, value, data_type="BOOLEAN", comment=""):
        score_calls.append({"name": name, "value": value, "data_type": data_type, "comment": comment})

    monkeypatch.setattr(LangfuseTracer, "is_enabled", lambda: True)
    monkeypatch.setattr(LangfuseTracer, "score_current_trace", fake_score_current_trace)

    provider = FakeGraderProvider(json.dumps({
        "result": "satisfied", "explanation": "ok",
        "criteria": [{"name": "lines", "passed": True}]
    }))
    mw = RubricMiddleware(grader_provider=provider)
    state = _make_state(rubric="- Three lines")
    await mw.aafter_agent(state)

    assert len(score_calls) == 1
    assert score_calls[0]["name"] == "rubric_satisfied"
    assert score_calls[0]["value"] == 1.0
    assert score_calls[0]["data_type"] == "BOOLEAN"
