"""Unit tests for RubricMiddleware."""

from __future__ import annotations

import json

import pytest

from src.sdk.messages import Message
from src.sdk.middleware_rubric import (
    GraderResponse,
    RubricMiddleware,
    _build_grader_transcript,
    _parse_grader_response,
    _revision_prompt,
)


class FakeGraderProvider:
    def __init__(self, response_json: str):
        self._response_json = response_json
        self.call_count = 0
        self.model_id = "test:grader"

    async def chat(self, messages, tools=None, model=None, provider_options=None, **kwargs):
        self.call_count += 1
        return Message.assistant(content=self._response_json)

    async def chat_stream(self, *args, **kwargs):
        raise NotImplementedError

    async def list_models(self):
        return []


@pytest.mark.asyncio
async def test_rubric_middleware_grade_returns_satisfied():
    provider = FakeGraderProvider(json.dumps({
        "result": "satisfied", "explanation": "all good",
        "criteria": [{"name": "three lines", "passed": True}]
    }))
    mw = RubricMiddleware(provider, "- Three lines")
    result = await mw.grade([Message.user("write a haiku")], 0)
    assert result["result"] == "satisfied"
    assert provider.call_count == 1
    assert "grading_run_id" in result


@pytest.mark.asyncio
async def test_rubric_middleware_grade_returns_needs_revision():
    provider = FakeGraderProvider(json.dumps({
        "result": "needs_revision", "explanation": "not enough lines",
        "criteria": [{"name": "three lines", "passed": False, "gap": "only two lines"}]
    }))
    mw = RubricMiddleware(provider, "- Three lines")
    result = await mw.grade([Message.user("write a haiku")], 0)
    assert result["result"] == "needs_revision"
    assert "grading_run_id" in result


@pytest.mark.asyncio
async def test_rubric_middleware_grade_returns_grader_error_on_malformed_json():
    provider = FakeGraderProvider("this is not json")
    mw = RubricMiddleware(provider, "- Three lines")
    result = await mw.grade([Message.user("write a haiku")], 0)
    assert result["result"] == "grader_error"
    assert "grading_run_id" in result


@pytest.mark.asyncio
async def test_rubric_middleware_grade_returns_grader_error_on_provider_exception():
    class FailingProvider:
        async def chat(self, messages, **kwargs):
            raise RuntimeError("provider down")

    mw = RubricMiddleware(FailingProvider(), "- Three lines")
    result = await mw.grade([Message.user("hi")], 0)
    assert result["result"] == "grader_error"
    assert "provider down" in result["explanation"]


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


def test_parse_grader_response_strips_code_fence():
    content = '```json\n{"result": "satisfied", "explanation": "ok", "criteria": []}\n```'
    result = _parse_grader_response(content)
    assert result.result == "satisfied"


def test_revision_prompt_includes_failing_criteria():
    evaluation = {
        "result": "needs_revision",
        "explanation": "not enough lines",
        "criteria": [{"name": "three lines", "passed": False, "gap": "only two lines"}],
    }
    prompt = _revision_prompt(evaluation)
    assert "three lines" in prompt
    assert "only two lines" in prompt


def test_build_grader_transcript_empty():
    assert _build_grader_transcript([]) == "(empty transcript)"


def test_build_grader_transcript_includes_messages():
    messages = [
        Message.user("write a haiku"),
        Message.assistant(content="a haiku here"),
    ]
    transcript = _build_grader_transcript(messages)
    assert "write a haiku" in transcript
    assert "a haiku here" in transcript


def test_rubric_middleware_max_iterations():
    mw = RubricMiddleware(FakeGraderProvider("{}"), "- Three lines", max_iterations=5)
    assert mw.max_iterations == 5


def test_rubric_middleware_default_max_iterations():
    mw = RubricMiddleware(FakeGraderProvider("{}"), "- Three lines")
    assert mw.max_iterations == 3
