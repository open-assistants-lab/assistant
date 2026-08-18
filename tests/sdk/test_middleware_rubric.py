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


def test_parse_grader_response_extracts_json_from_prose():
    content = (
        'Here is my evaluation: '
        '{"result": "needs_revision", "explanation": "fix it", '
        '"criteria": [{"name": "x", "passed": false, "gap": "g"}]} '
        'Hope that helps!'
    )
    result = _parse_grader_response(content)
    assert result.result == "needs_revision"
    assert result.criteria[0]["gap"] == "g"


def test_parse_grader_response_empty_raises():
    with pytest.raises(ValueError, match="empty response"):
        _parse_grader_response("")


def test_parse_grader_response_garbage_raises():
    with pytest.raises(ValueError, match="not valid JSON"):
        _parse_grader_response("I have no idea what the rubric means")


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


def test_rubric_middleware_grader_model_id_from_explicit_arg() -> None:
    """Regression: real LLMProvider subclasses (OllamaCloud, OpenAIProvider, ...)
    do NOT carry a `model_id` attribute — only AgentLoop does. The middleware
    must not read provider.model_id; it must use the explicit grader_model_id
    passed at construction.
    """
    provider = FakeGraderProvider("{}")
    # Simulate a real provider: delete the test-only model_id attribute.
    del provider.model_id
    mw = RubricMiddleware(provider, "- Three lines", grader_model_id="ollama:minimax-m2.5")
    assert mw.grader_model_id == "ollama:minimax-m2.5"


def test_rubric_middleware_grader_model_id_without_model_id_attr_or_arg() -> None:
    """A provider with no model_id attribute and no explicit grader_model_id
    must not raise AttributeError — it should fall back to a sentinel string
    so usage aggregation still works.
    """
    provider = FakeGraderProvider("{}")
    del provider.model_id
    mw = RubricMiddleware(provider, "- Three lines")
    assert isinstance(mw.grader_model_id, str)
    assert mw.grader_model_id  # non-empty


@pytest.mark.asyncio
async def test_load_rubric_middleware_uses_saved_grader_model(monkeypatch):
    """The user's saved verification.grader_model wins over host config."""
    from types import SimpleNamespace

    from src.config.user_settings import SavedUserSettings, VerificationOverrides
    from src.sdk.middleware_rubric import load_rubric_middleware

    captured = {}

    class FakeProvider:
        pass

    def fake_create_model_from_config(model, user_id=None):
        captured["model"] = model
        return FakeProvider()

    settings = SimpleNamespace(verification=SimpleNamespace(
        enabled=True,
        grader_model="openai:host-grader",
        max_iterations=3,
        default_rubric="- Non-empty",
    ))
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        "src.sdk.providers.factory.create_model_from_config", fake_create_model_from_config
    )
    monkeypatch.setattr(
        "src.config.user_settings_service.load_saved_user_settings",
        lambda user_id: SavedUserSettings(
            verification=VerificationOverrides(grader_model="anthropic:saved-grader")
        ),
    )

    class FakeLoop:
        model_id = "openai:agent"

    mw = await load_rubric_middleware("u", FakeLoop(), rubric="- Non-empty")
    assert mw is not None
    assert captured["model"] == "anthropic:saved-grader"


@pytest.mark.asyncio
async def test_load_rubric_middleware_falls_back_to_host_grader_model(monkeypatch):
    """Without a saved override, the host grader model is used."""
    from types import SimpleNamespace

    from src.config.user_settings import SavedUserSettings
    from src.sdk.middleware_rubric import load_rubric_middleware

    captured = {}

    class FakeProvider:
        pass

    def fake_create_model_from_config(model, user_id=None):
        captured["model"] = model
        return FakeProvider()

    settings = SimpleNamespace(verification=SimpleNamespace(
        enabled=True,
        grader_model="openai:host-grader",
        max_iterations=3,
        default_rubric="- Non-empty",
    ))
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        "src.sdk.providers.factory.create_model_from_config", fake_create_model_from_config
    )
    monkeypatch.setattr(
        "src.config.user_settings_service.load_saved_user_settings",
        lambda user_id: SavedUserSettings(),
    )

    class FakeLoop:
        model_id = "openai:agent"

    mw = await load_rubric_middleware("u", FakeLoop(), rubric="- Non-empty")
    assert mw is not None
    assert captured["model"] == "openai:host-grader"
