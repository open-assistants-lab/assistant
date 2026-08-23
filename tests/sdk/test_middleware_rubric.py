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


@pytest.mark.asyncio
async def test_grader_loop_bounds_output_tokens():
    """The grader's loop must cap output tokens so a verbose verdict can't
    blow up latency or hit the provider timeout."""
    provider = FakeGraderProvider("{}")
    mw = RubricMiddleware(provider, "- Three lines")
    loop = await mw._ensure_loop()
    opts = loop.run_config.provider_options or {}
    assert opts["ollama-cloud"]["max_tokens"] == 800
    assert opts["openai"]["max_tokens"] == 800


def test_revision_prompt_forbids_mentioning_the_grader():
    evaluation = {
        "result": "needs_revision",
        "explanation": "too short",
        "criteria": [{"name": "non-empty", "passed": False, "gap": "add detail"}],
    }
    prompt = _revision_prompt(evaluation)
    # The revision must apply the feedback silently — the answer must never
    # reference the grader, the rubric, or the revision process.
    assert "must not mention the grader" in prompt
    assert "must not mention" in prompt


# ---------------------------------------------------------------------------
# C11 — selective verification (auto mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_mode_skips_grader_for_trivial_turn(monkeypatch):
    """mode=auto + no tools + short answer → grader never called."""
    from src.sdk import middleware_rubric as _mw
    from src.sdk.loop import AgentLoop
    from src.sdk.runner import run_with_verification
    from tests.sdk.test_sdk_loop import MockProvider

    provider = MockProvider(responses=[Message.assistant(content="Hi!")])
    loop = AgentLoop(provider=provider, tools=[])
    grader = FakeGraderProvider(json.dumps({"result": "satisfied", "explanation": "", "criteria": []}))
    fake_mw = RubricMiddleware(grader, "- Be nice")

    async def fake_load(user_id, loop, rubric):
        return fake_mw

    monkeypatch.setattr(_mw, "load_rubric_middleware", fake_load)

    vresult = await run_with_verification(
        loop, [Message.user("hi")], "u", "s", rubric="- Be nice", mode="auto"
    )

    assert vresult.rubric_status == "skipped"
    assert grader.call_count == 0
    assert vresult.rubric_available is True


@pytest.mark.asyncio
async def test_auto_mode_verifies_when_tools_used(monkeypatch):
    """Trivial-looking but tool-using turn → grader runs."""
    from src.sdk import middleware_rubric as _mw
    from src.sdk.loop import AgentLoop
    from src.sdk.messages import ToolCall
    from src.sdk.runner import run_with_verification
    from tests.sdk.test_sdk_loop import MockProvider, echo

    provider = MockProvider(
        responses=[
            Message.assistant(
                content="",
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hi"})],
            ),
            Message.assistant(content="done"),
        ]
    )
    loop = AgentLoop(provider=provider, tools=[echo])
    grader = FakeGraderProvider(json.dumps({"result": "satisfied", "explanation": "", "criteria": []}))
    fake_mw = RubricMiddleware(grader, "- Be nice")

    async def fake_load(user_id, loop, rubric):
        return fake_mw

    monkeypatch.setattr(_mw, "load_rubric_middleware", fake_load)

    vresult = await run_with_verification(
        loop, [Message.user("hi")], "u", "s", rubric="- Be nice", mode="auto"
    )

    assert vresult.rubric_status == "satisfied"
    assert grader.call_count == 1


@pytest.mark.asyncio
async def test_auto_mode_verifies_when_response_has_code(monkeypatch):
    """Short answer but with a code fence → grader runs."""
    from src.sdk import middleware_rubric as _mw
    from src.sdk.loop import AgentLoop
    from src.sdk.runner import run_with_verification
    from tests.sdk.test_sdk_loop import MockProvider

    provider = MockProvider(responses=[Message.assistant(content="```python\nx=1\n```")])
    loop = AgentLoop(provider=provider, tools=[])
    grader = FakeGraderProvider(json.dumps({"result": "satisfied", "explanation": "", "criteria": []}))
    fake_mw = RubricMiddleware(grader, "- Be nice")

    async def fake_load(user_id, loop, rubric):
        return fake_mw

    monkeypatch.setattr(_mw, "load_rubric_middleware", fake_load)

    vresult = await run_with_verification(
        loop, [Message.user("write code")], "u", "s", rubric="- Be nice", mode="auto"
    )

    assert vresult.rubric_status == "satisfied"
    assert grader.call_count == 1


@pytest.mark.asyncio
async def test_on_mode_always_verifies(monkeypatch):
    """Explicit mode=on ignores auto-skip conditions."""
    from src.sdk import middleware_rubric as _mw
    from src.sdk.loop import AgentLoop
    from src.sdk.runner import run_with_verification
    from tests.sdk.test_sdk_loop import MockProvider

    provider = MockProvider(responses=[Message.assistant(content="Hi!")])
    loop = AgentLoop(provider=provider, tools=[])
    grader = FakeGraderProvider(json.dumps({"result": "satisfied", "explanation": "", "criteria": []}))
    fake_mw = RubricMiddleware(grader, "- Be nice")

    async def fake_load(user_id, loop, rubric):
        return fake_mw

    monkeypatch.setattr(_mw, "load_rubric_middleware", fake_load)

    vresult = await run_with_verification(
        loop, [Message.user("hi")], "u", "s", rubric="- Be nice", mode="on"
    )

    assert vresult.rubric_status == "satisfied"
    assert grader.call_count == 1


# ---------------------------------------------------------------------------
# E — verification stage timings accumulate across attempts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verification_timings_accumulate_across_attempts():
    """Multi-attempt runs sum grade durations (verification_count == grades)."""
    from src.sdk.harness_timings import HarnessTimings
    from src.sdk.loop import AgentLoop

    agent_loop = AgentLoop(provider=FakeGraderProvider(""), tools=[])
    agent_loop.timings = HarnessTimings()

    provider = FakeGraderProvider(json.dumps({"result": "satisfied", "explanation": "", "criteria": []}))
    mw = RubricMiddleware(provider, "- Be nice", agent_loop=agent_loop)

    await mw.grade([Message.user("u")], 0)
    await mw.grade([Message.user("y")], 1)

    assert agent_loop.timings.count("verification") == 2
    assert agent_loop.timings.stage_ms("verification") is not None
    assert agent_loop.timings.stage_ms("verification") > 0


@pytest.mark.asyncio
async def test_verification_timings_absent_without_grades():
    from src.sdk.harness_timings import HarnessTimings
    from src.sdk.loop import AgentLoop

    agent_loop = AgentLoop(provider=FakeGraderProvider(""), tools=[])
    agent_loop.timings = HarnessTimings()

    provider = FakeGraderProvider(json.dumps({"result": "satisfied", "explanation": "", "criteria": []}))
    mw = RubricMiddleware(provider, "- Be nice", agent_loop=agent_loop)

    assert agent_loop.timings.count("verification") == 0
    assert agent_loop.timings.stage_ms("verification") is None
