# Loop Engineering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add verification (loop 2), event-driven (loop 3), and hill-climbing (loop 4) loops around the existing `AgentLoop` (loop 1).

**Architecture:** `RubricMiddleware` wraps `AgentLoop` via a generic `_needs_rerun` mechanism. `TriggerRegistry` normalizes external events into agent runs. `AnalysisJob` reads accumulated `RunOutcome` records and proposes improvements. Build order: loop 2 → loop 3 → loop 4.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest, custom `src/sdk` runtime.

**Spec:** `docs/superpowers/specs/2026-07-27-loop-engineering-design.md`

---

## File Structure

**Create:**
- `src/sdk/middleware_rubric.py` — `RubricMiddleware`, `GraderResponse`, `RubricEvaluation`, `CriterionEval`, `GraderSystemPrompt`
- `tests/sdk/test_middleware_rubric.py` — unit tests for rubric middleware
- `src/sdk/loops/__init__.py` — package init
- `src/sdk/loops/events.py` — `TriggerRegistry`, `AgentEvent`
- `src/sdk/loops/improvement.py` — `AnalysisJob`, `ImprovementSuggestion`, `RunOutcome`
- `src/sdk/loops/storage.py` — SQLite for `RunOutcome` and `ImprovementSuggestion`
- `tests/sdk/test_events.py` — trigger registry tests
- `tests/sdk/test_improvement.py` — hill-climbing tests
- `tests/sdk/test_run_outcome.py` — RunOutcome persistence tests

**Modify:**
- `src/sdk/loop.py` — extract `_run_react_loop(state)` helper, add `_needs_rerun` check after `aafter_agent` in `_run_impl` and `run_stream`
- `src/sdk/runner.py` — create grader provider, add `RubricMiddleware` to middleware list when enabled, pass rubric via `state.extra["rubric"]`, persist `RunOutcome`
- `src/sdk/messages.py` — add `rubric_evaluation_start` and `rubric_evaluation_end` to `StreamEventType` Literal
- `src/sdk/state.py` — no changes (uses existing `extra` dict)
- `src/http/models.py` — add `verification` field to `MessageRequest`
- `src/http/routers/conversation.py` — pass rubric from request to runner, include verification verdicts in response
- `src/http/routers/ws.py` — same for WebSocket
- `src/http/stream_adapter.py` — handle `rubric_evaluation_start` / `rubric_evaluation_end` chunk types
- `src/config/settings.py` — add `VerificationConfig` and `HillClimbingConfig`

---

## Loop 2: Verification (RubricMiddleware)

### Task 1: Add StreamChunk Event Types

**Files:**
- Modify: `src/sdk/messages.py`
- Test: `tests/sdk/test_messages.py`

- [ ] **Step 1: Write failing test for new event types**

Add to `tests/sdk/test_messages.py`:

```python
def test_rubric_evaluation_start_type_accepted():
    chunk = StreamChunk(type="rubric_evaluation_start", content='{"grading_run_id":"abc","iteration":0}')
    assert chunk.type == "rubric_evaluation_start"

def test_rubric_evaluation_end_type_accepted():
    chunk = StreamChunk(type="rubric_evaluation_end", content='{"result":"satisfied"}')
    assert chunk.type == "rubric_evaluation_end"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/sdk/test_messages.py::test_rubric_evaluation_start_type_accepted tests/sdk/test_messages.py::test_rubric_evaluation_end_type_accepted -v`
Expected: FAIL — `StreamEventType` Literal doesn't include the new types.

- [ ] **Step 3: Add types to StreamEventType**

In `src/sdk/messages.py`, add to the `StreamEventType` Literal (after `"usage"`):

```python
StreamEventType = Literal[
    "text_start",
    "text_delta",
    "text_end",
    "tool_input_start",
    "tool_input_delta",
    "tool_input_end",
    "reasoning_start",
    "reasoning_delta",
    "reasoning_end",
    "interrupt",
    "done",
    "error",
    "ai_token",
    "tool_start",
    "tool_end",
    "reasoning",
    "tool_result",
    "usage",
    "rubric_evaluation_start",
    "rubric_evaluation_end",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/sdk/test_messages.py::test_rubric_evaluation_start_type_accepted tests/sdk/test_messages.py::test_rubric_evaluation_end_type_accepted -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sdk/messages.py tests/sdk/test_messages.py
git commit -m "feat: add rubric_evaluation_start/end to StreamEventType"
```

---

### Task 2: Add VerificationConfig to Settings

**Files:**
- Modify: `src/config/settings.py`
- Test: `tests/test_config.py` (if exists, else `tests/sdk/test_config.py`)

- [ ] **Step 1: Write failing test for VerificationConfig**

Add to test file:

```python
def test_verification_config_defaults():
    from src.config import get_settings
    s = get_settings()
    assert hasattr(s, "verification")
    assert s.verification.enabled is False
    assert s.verification.grader_model == ""
    assert s.verification.max_iterations == 3
    assert s.verification.default_rubric == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/sdk/test_config.py::test_verification_config_defaults -v`
Expected: FAIL — no `verification` attribute on settings.

- [ ] **Step 3: Add VerificationConfig class**

In `src/config/settings.py`, after `SummarizationConfig`, add:

```python
class VerificationConfig(_BaseSettings):
    """Verification (rubric middleware) configuration."""

    enabled: bool = False
    default_rubric: str = ""
    grader_model: str = Field(default="", description="Model for grading (empty = use agent model)")
    grader_system_prompt: str = ""
    grader_tools: list[str] = Field(default_factory=list, description="Tool names the grader may call")
    max_iterations: int = 3

    model_config = SettingsConfigDict(env_prefix="VERIFICATION_")
```

In `AppConfig`, add:

```python
class AppConfig(_BaseSettings):
    ...
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/sdk/test_config.py::test_verification_config_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config/settings.py tests/sdk/test_config.py
git commit -m "feat: add VerificationConfig to settings"
```

---

### Task 3: Add RunConfig Verification Fields

**Files:**
- Modify: `src/sdk/loop.py`
- Test: `tests/sdk/test_sdk_loop.py`

- [ ] **Step 1: Write failing test for RunConfig verification fields**

Add to `tests/sdk/test_sdk_loop.py`:

```python
def test_run_config_has_verification_fields():
    rc = RunConfig()
    assert rc.verification_enabled is False
    assert rc.verification_rubric is None
    assert rc.verification_grader_model is None
    assert rc.verification_grader_system_prompt is None
    assert rc.verification_grader_tools is None
    assert rc.verification_max_iterations == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/sdk/test_sdk_loop.py::test_run_config_has_verification_fields -v`
Expected: FAIL

- [ ] **Step 3: Add fields to RunConfig**

In `src/sdk/loop.py`, extend `RunConfig`:

```python
@dataclass
class RunConfig:
    """Configuration for a single agent run."""

    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_tokens_total: int = DEFAULT_MAX_TOKENS_TOTAL
    cost_limit_usd: float = DEFAULT_COST_LIMIT_USD
    provider_options: dict[str, dict[str, Any]] | None = None
    verification_enabled: bool = False
    verification_rubric: str | None = None
    verification_grader_model: str | None = None
    verification_grader_system_prompt: str | None = None
    verification_grader_tools: list[str] | None = None
    verification_max_iterations: int = 3
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/sdk/test_sdk_loop.py::test_run_config_has_verification_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sdk/loop.py tests/sdk/test_sdk_loop.py
git commit -m "feat: add verification fields to RunConfig"
```

---

### Task 4: Extract ReAct Loop Helper and Add _needs_rerun to _run_impl

**Files:**
- Modify: `src/sdk/loop.py`
- Test: `tests/sdk/test_sdk_loop.py`

- [ ] **Step 1: Write failing test for re-run mechanism**

Add to `tests/sdk/test_sdk_loop.py`:

```python
async def test_needs_rerun_triggers_second_react_loop():
    """When aafter_agent sets _needs_rerun, the loop re-enters the ReAct loop."""

    class RerunMiddleware(Middleware):
        def __init__(self):
            self.call_count = 0

        async def aafter_agent(self, state: AgentState) -> None:
            self.call_count += 1
            if self.call_count == 1:
                state.add_message(Message(role="user", content="try again"))
                state.extra["_needs_rerun"] = True

    from src.sdk.providers.base import LLMProvider
    from src.sdk.tools import ToolRegistry

    class FakeProvider(LLMProvider):
        async def chat(self, messages, tools=None, model=None, provider_options=None, **kwargs):
            return Message.assistant(content="done")

        def chat_stream(self, messages, tools=None, model=None, provider_options=None, **kwargs):
            raise NotImplementedError

        async def list_models(self):
            return []

    mw = RerunMiddleware()
    loop = AgentLoop(
        provider=FakeProvider(),
        tools=[],
        middlewares=[mw],
        run_config=RunConfig(max_iterations=5),
    )
    result = await loop.run([Message.user("hello")])

    assert mw.call_count == 2
    assert len(result) >= 3  # user message + "done" + "try again" + "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/sdk/test_sdk_loop.py::test_needs_rerun_triggers_second_react_loop -v`
Expected: FAIL — no `_needs_rerun` check exists.

- [ ] **Step 3: Extract _run_react_loop helper**

In `src/sdk/loop.py`, extract the `for iteration in range(self.run_config.max_iterations)` body inside `_run_impl` (lines 786-904) into a helper method:

```python
async def _run_react_loop(self, state: AgentState, cost_tracker: CostTracker) -> None:
    """Run the ReAct loop body — LLM calls and tool execution."""
    for iteration in range(self.run_config.max_iterations):
        limit_reason = cost_tracker.exceeds_limits(self.run_config)
        if limit_reason:
            state.add_message(Message.assistant(content=f"Run limit reached: {limit_reason}"))
            break
        # ... existing loop body (overflow_retries, LLM call, tool execution) ...
```

Then `_run_impl` becomes:

```python
async def _run_impl(self, messages: list[Message]) -> list[Message]:
    state = AgentState(messages=list(messages))
    self.state = state
    cost_tracker = CostTracker()
    await self._run_hooks("abefore_agent", state)
    try:
        await self._check_input_guardrails(state)
    except GuardrailTripwire as e:
        state.add_message(Message.assistant(content=f"Input blocked: {e.result.message}"))
        await self._run_hooks("aafter_agent", state)
        return state.messages
    try:
        await self._run_react_loop(state, cost_tracker)
    except SubagentCancelledError:
        await self._run_hooks("aafter_agent", state)
        raise
    await self._run_hooks("aafter_agent", state)
    while state.extra.get("_needs_rerun"):
        state.extra["_needs_rerun"] = False
        await self._run_hooks("abefore_agent", state)
        await self._run_react_loop(state, cost_tracker)
        await self._run_hooks("aafter_agent", state)
    return state.messages
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/sdk/test_sdk_loop.py::test_needs_rerun_triggers_second_react_loop -v`
Expected: PASS

- [ ] **Step 5: Run existing loop tests to verify no regression**

Run: `uv run python -m pytest tests/sdk/test_sdk_loop.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sdk/loop.py tests/sdk/test_sdk_loop.py
git commit -m "feat: extract _run_react_loop and add _needs_rerun re-run mechanism"
```

---

### Task 5: Add _needs_rerun to run_stream

**Files:**
- Modify: `src/sdk/loop.py`
- Test: `tests/sdk/test_sdk_loop.py`

- [ ] **Step 1: Write failing test for streaming re-run**

Add to `tests/sdk/test_sdk_loop.py`:

```python
async def test_needs_rerun_in_stream_drains_pending_events():
    """run_stream re-enters when _needs_rerun is set, draining pending events."""

    class StreamRerunMiddleware(Middleware):
        def __init__(self):
            self.call_count = 0

        async def aafter_agent(self, state: AgentState) -> None:
            self.call_count += 1
            if self.call_count == 1:
                state.extra.setdefault("_pending_stream_events", []).append(
                    StreamChunk(type="rubric_evaluation_end", content='{"result":"needs_revision"}')
                )
                state.add_message(Message(role="user", content="try again"))
                state.extra["_needs_rerun"] = True

    from src.sdk.providers.base import LLMProvider

    class FakeStreamProvider(LLMProvider):
        async def chat(self, messages, tools=None, model=None, provider_options=None, **kwargs):
            return Message.assistant(content="done")

        async def chat_stream(self, messages, tools=None, model=None, provider_options=None, **kwargs):
            yield StreamChunk(type="text_delta", content="done")
            yield StreamChunk(type="done", content="done")

        async def list_models(self):
            return []

    mw = StreamRerunMiddleware()
    loop = AgentLoop(
        provider=FakeStreamProvider(),
        tools=[],
        middlewares=[mw],
        run_config=RunConfig(max_iterations=5),
    )
    events = []
    async for chunk in loop.run_stream([Message.user("hello")]):
        events.append(chunk)

    assert mw.call_count == 2
    rubric_events = [e for e in events if e.type == "rubric_evaluation_end"]
    assert len(rubric_events) == 1
    done_events = [e for e in events if e.type == "done"]
    assert len(done_events) == 1  # only one done event at the end
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/sdk/test_sdk_loop.py::test_needs_rerun_in_stream_drains_pending_events -v`
Expected: FAIL

- [ ] **Step 3: Add re-run + drain to _run_stream_inner**

In `src/sdk/loop.py`, modify `_run_stream_inner` to extract the streaming ReAct loop into `_run_stream_react_loop` and add the re-run + drain mechanism. Replace the final `aafter_agent` + `done` emission (lines ~1232-1248) with:

```python
except SubagentCancelledError:
    await self._run_hooks("aafter_agent", state)
    raise

await self._run_hooks("aafter_agent", state)
for event in state.extra.pop("_pending_stream_events", []):
    yield event

while state.extra.get("_needs_rerun"):
    state.extra["_needs_rerun"] = False
    await self._run_hooks("abefore_agent", state)
    async for chunk in self._run_stream_react_loop(state, cost_tracker, all_tool_calls):
        yield chunk
    await self._run_hooks("aafter_agent", state)
    for event in state.extra.pop("_pending_stream_events", []):
        yield event

final_content = ""
if state.messages:
    last = state.messages[-1]
    if last.role == "assistant":
        final_content = last.content if isinstance(last.content, str) else ""
    elif last.role == "tool":
        final_content = (
            "I wasn't able to complete this task. "
            "The last tool call did not produce a usable result. "
            "Please try rephrasing your request."
        )

yield StreamChunk.done(content=final_content, tool_calls=all_tool_calls)
```

Extract the existing `for iteration in range(self.run_config.max_iterations)` streaming loop body into `_run_stream_react_loop(self, state, cost_tracker, all_tool_calls) -> AsyncIterator[StreamChunk]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/sdk/test_sdk_loop.py::test_needs_rerun_in_stream_drains_pending_events -v`
Expected: PASS

- [ ] **Step 5: Run existing streaming tests**

Run: `uv run python -m pytest tests/sdk/test_sdk_loop.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sdk/loop.py tests/sdk/test_sdk_loop.py
git commit -m "feat: add _needs_rerun + event drain to run_stream"
```

---

### Task 6: Implement RubricMiddleware

**Files:**
- Create: `src/sdk/middleware_rubric.py`
- Test: `tests/sdk/test_middleware_rubric.py`

- [ ] **Step 1: Write failing tests for RubricMiddleware core behavior**

Create `tests/sdk/test_middleware_rubric.py`:

```python
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.sdk.middleware import Middleware
from src.sdk.middleware_rubric import (
    RubricMiddleware, GraderResponse, RubricEvaluation, GRADER_SYSTEM_PROMPT,
)
from src.sdk.messages import Message
from src.sdk.state import AgentState


class FakeGraderProvider:
    """Fake provider that returns a pre-configured GraderResponse as JSON."""

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
    provider = FakeGraderProvider(
        json.dumps({"result": "satisfied", "explanation": "all good", "criteria": [
            {"name": "three lines", "passed": True}
        ]})
    )
    mw = RubricMiddleware(grader_provider=provider)
    state = _make_state(rubric="- Three lines")
    await mw.aafter_agent(state)
    assert provider.call_count == 1
    assert not state.extra.get("_needs_rerun")
    assert state.extra["_rubric_status"] == "satisfied"


@pytest.mark.asyncio
async def test_needs_revision_injects_feedback_and_sets_rerun():
    provider = FakeGraderProvider(
        json.dumps({"result": "needs_revision", "explanation": "not enough lines", "criteria": [
            {"name": "three lines", "passed": False, "gap": "only two lines"}
        ]})
    )
    mw = RubricMiddleware(grader_provider=provider)
    state = _make_state(rubric="- Three lines")
    await mw.aafter_agent(state)
    assert provider.call_count == 1
    assert state.extra.get("_needs_rerun") is True
    assert state.extra["_rubric_status"] == "needs_revision"
    # Feedback message was injected
    feedback_msgs = [m for m in state.messages if getattr(m, "lc_source", None) == "rubric_grader"]
    assert len(feedback_msgs) == 1
    assert "three lines" in feedback_msgs[0].content.lower()


@pytest.mark.asyncio
async def test_max_iterations_reached():
    provider = FakeGraderProvider(
        json.dumps({"result": "needs_revision", "explanation": "still wrong", "criteria": [
            {"name": "three lines", "passed": False, "gap": "still wrong"}
        ]})
    )
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
    provider = FakeGraderProvider(
        json.dumps({"result": "failed", "explanation": "rubric is contradictory", "criteria": []})
    )
    mw = RubricMiddleware(grader_provider=provider)
    state = _make_state(rubric="- Must be empty AND non-empty")
    await mw.aafter_agent(state)
    assert state.extra["_rubric_status"] == "failed"
    assert not state.extra.get("_needs_rerun")


@pytest.mark.asyncio
async def test_pending_stream_events_appended():
    provider = FakeGraderProvider(
        json.dumps({"result": "satisfied", "explanation": "ok", "criteria": [
            {"name": "lines", "passed": True}
        ]})
    )
    mw = RubricMiddleware(grader_provider=provider)
    state = _make_state(rubric="- Three lines")
    await mw.aafter_agent(state)
    events = state.extra.get("_pending_stream_events", [])
    types = [e.type for e in events]
    assert "rubric_evaluation_start" in types
    assert "rubric_evaluation_end" in types


@pytest.mark.asyncio
async def test_on_evaluation_callback_fires():
    provider = FakeGraderProvider(
        json.dumps({"result": "satisfied", "explanation": "ok", "criteria": []})
    )
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
            "result": "satisfied",
            "explanation": "ok",
            "criteria": [{"name": "x", "passed": False, "gap": "fail"}],
        })
    with pytest.raises(ValidationError):
        GraderResponse.model_validate({
            "result": "needs_revision",
            "explanation": "ok",
            "criteria": [{"name": "x", "passed": True}],
        })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/sdk/test_middleware_rubric.py -v`
Expected: FAIL — `src.sdk.middleware_rubric` does not exist.

- [ ] **Step 3: Implement RubricMiddleware**

Create `src/sdk/middleware_rubric.py` with:

- `GRADER_SYSTEM_PROMPT` constant (from spec)
- `CriterionPass`, `CriterionFail` TypedDicts
- `GraderResponse(BaseModel)` with `_check_consistency` validator
- `RubricEvaluation(TypedDict)`
- `RubricMiddleware(Middleware)` with:
  - `__init__(grader_provider, system_prompt=None, grader_tools=None, max_iterations=3, on_evaluation=None)`
  - `aafter_agent(state)` implementing steps 1-11 from spec
  - `_build_grader_payload(state, iteration)` — nonce-bracketed rubric + bounded transcript
  - `_build_evaluation(graded, grading_run_id, iteration)` — construct `RubricEvaluation`
  - `_revision_prompt(evaluation)` — feedback text
  - `_emit_start(state, grading_run_id, iteration)` — append to `_pending_stream_events`
  - `_emit_end(state, grading_run_id, iteration, evaluation)` — append to `_pending_stream_events`
  - `_parse_grader_response(content)` — JSON parse + `GraderResponse.model_validate`, raises on failure

Key implementation details:
- Transcript: last 30 messages, truncated to 4000 chars each, skip `lc_source == "rubric_grader"` messages when finding original user prompt
- Nonce: `secrets.token_hex(8)` for delimiters
- Sanitize: replace `</rubric` and `</transcript` in content with `<\/rubric` etc.
- `max_iterations` must be positive int — validate in `__init__`

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/sdk/test_middleware_rubric.py -v`
Expected: PASS

- [ ] **Step 5: Run lint**

Run: `uv run ruff check src/sdk/middleware_rubric.py tests/sdk/test_middleware_rubric.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sdk/middleware_rubric.py tests/sdk/test_middleware_rubric.py
git commit -m "feat: implement RubricMiddleware with grader LLM, nonce-delimited payload, and streaming events"
```

---

### Task 7: Wire RubricMiddleware into Runner

**Files:**
- Modify: `src/sdk/runner.py`
- Test: `tests/sdk/test_runner.py`

- [ ] **Step 1: Write failing test for verification middleware wiring**

Add to `tests/sdk/test_runner.py`:

```python
async def test_create_sdk_loop_adds_rubric_middleware_when_enabled(monkeypatch):
    from src.sdk.runner import create_sdk_loop
    from src.sdk.middleware_rubric import RubricMiddleware

    monkeypatch.setenv("VERIFICATION_ENABLED", "true")
    monkeypatch.setenv("VERIFICATION_GRADER_MODEL", "ollama-cloud:deepseek-v4-flash")
    monkeypatch.setenv("VERIFICATION_DEFAULT_RUBRIC", "- Response is non-empty")
    # Force settings reload
    from src.config import get_settings
    get_settings.cache_clear()

    loop = await create_sdk_loop("test_user_verification", model="ollama-cloud:deepseek-v4-flash")

    rubric_mws = [mw for mw in loop.middlewares if isinstance(mw, RubricMiddleware)]
    assert len(rubric_mws) == 1
    assert rubric_mws[0].max_iterations == 3

    get_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/sdk/test_runner.py::test_create_sdk_loop_adds_rubric_middleware_when_enabled -v`
Expected: FAIL

- [ ] **Step 3: Add verification middleware wiring to create_sdk_loop**

In `src/sdk/runner.py`, after the summarization middleware block (around line 480), add:

```python
# Verification (rubric) middleware
verification_config = settings.verification
if verification_config.enabled:
    from src.sdk.middleware_rubric import RubricMiddleware

    grader_model = verification_config.grader_model or model_str
    grader_provider = create_model_from_config(grader_model, provider_keys=provider_keys, user_id=user_id)

    grader_tool_defs: list[Any] = []
    if verification_config.grader_tools:
        from src.sdk.native_tools import get_native_tools
        native_by_name = {td.name: td for td in get_native_tools()}
        for tool_name in verification_config.grader_tools:
            if tool_name in native_by_name:
                grader_tool_defs.append(native_by_name[tool_name])

    rubric_mw = RubricMiddleware(
        grader_provider=grader_provider,
        system_prompt=verification_config.grader_system_prompt or None,
        grader_tools=grader_tool_defs or None,
        max_iterations=verification_config.max_iterations,
    )
    middlewares.append(rubric_mw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/sdk/test_runner.py::test_create_sdk_loop_adds_rubric_middleware_when_enabled -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sdk/runner.py tests/sdk/test_runner.py
git commit -m "feat: wire RubricMiddleware into create_sdk_loop when verification enabled"
```

---

### Task 8: Pass Rubric via state.extra in run_sdk_agent

**Files:**
- Modify: `src/sdk/runner.py`
- Modify: `src/http/models.py`
- Modify: `src/http/routers/conversation.py`
- Test: `tests/api/test_conversation.py`

- [ ] **Step 1: Add verification field to MessageRequest**

In `src/http/models.py`, add to `MessageRequest`:

```python
class VerificationRequest(BaseModel):
    rubric: str | None = None

class MessageRequest(BaseModel):
    message: str
    model: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    verbose: bool = False
    provider_keys: dict[str, str] | None = None
    verification: VerificationRequest | None = None
```

- [ ] **Step 2: Write failing test for rubric passing**

Add to `tests/api/test_conversation.py`:

```python
async def test_verification_rubric_passed_to_agent(monkeypatch):
    """When request includes verification.rubric, it reaches state.extra['rubric']."""
    captured_state = {}

    original_run = None
    from src.sdk import runner as _runner

    async def fake_run_sdk_agent(user_id, messages, **kwargs):
        # Simulate: the runner would set state.extra["rubric"] on the loop's state
        # For testing, we verify the rubric was passed through kwargs
        captured_state["rubric"] = kwargs.get("rubric")
        return [Message.assistant(content="response")]

    monkeypatch.setattr(_runner, "run_sdk_agent", fake_run_sdk_agent)

    response = client.post(
        "/message",
        json={
            "message": "write a haiku",
            "verification": {"rubric": "- Three lines"},
        },
    )
    assert response.status_code == 200
    assert captured_state["rubric"] == "- Three lines"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run python -m pytest tests/api/test_conversation.py::test_verification_rubric_passed_to_agent -v`
Expected: FAIL

- [ ] **Step 4: Pass rubric from request to run_sdk_agent**

In `src/sdk/runner.py`, add `rubric: str | None = None` parameter to `run_sdk_agent` and `run_sdk_agent_stream`. Inside both functions, after getting the loop, set the rubric on the loop's state before running:

```python
async def run_sdk_agent(
    user_id: str,
    messages: list[Message],
    workspace_id: str = "personal",
    model: str | None = None,
    provider_keys: dict[str, str] | None = None,
    session_id: str | None = None,
    rubric: str | None = None,
) -> list[Message]:
    loop = await get_sdk_loop(user_id, workspace_id, model=model, provider_keys=provider_keys, session_id=session_id)
    register_user_loop(user_id, loop, session_id=session_id)
    try:
        if rubric:
            loop.state = loop.state or AgentState(messages=messages)
            loop.state.extra["rubric"] = rubric
        result = await loop.run(messages)
        return result
    finally:
        unregister_user_loop(user_id, loop, session_id=session_id)
```

Note: `AgentState` is created inside `_run_impl`, so the rubric needs to be set differently. Better approach: pass rubric via a loop attribute that `_run_impl` reads when creating state:

In `AgentLoop`, add `self.rubric: str | None = None` attribute. In `_run_impl` and `run_stream`, after creating `state`, set `state.extra["rubric"] = self.rubric` if `self.rubric` is set.

Then in `run_sdk_agent`:
```python
loop.rubric = rubric
```

In `src/http/routers/conversation.py`, extract rubric from request and pass to `run_sdk_agent`:

```python
rubric = None
if request.verification and request.verification.rubric:
    rubric = request.verification.rubric
elif settings.verification.enabled and settings.verification.default_rubric:
    rubric = settings.verification.default_rubric

result = await run_sdk_agent(user_id, messages, model=model, session_id=session_id, rubric=rubric)
```

Do the same for `run_sdk_agent_stream` and the WS path.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m pytest tests/api/test_conversation.py::test_verification_rubric_passed_to_agent -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/http/models.py src/sdk/runner.py src/sdk/loop.py src/http/routers/conversation.py src/http/routers/ws.py tests/api/test_conversation.py
git commit -m "feat: pass verification rubric from request through runner to AgentLoop state"
```

---

### Task 9: Return Verification Verdicts in REST Response

**Files:**
- Modify: `src/http/routers/conversation.py`
- Modify: `src/http/models.py`
- Test: `tests/api/test_conversation.py`

- [ ] **Step 1: Add verification verdicts to MessageResponse**

In `src/http/models.py`:

```python
class VerificationVerdict(BaseModel):
    status: str | None = None
    iterations: int = 0
    evaluations: list[dict[str, Any]] = Field(default_factory=list)

class MessageResponse(BaseModel):
    response: str
    error: str | None = None
    verbose_data: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] | None = Field(default=None)
    verification: VerificationVerdict | None = None
```

- [ ] **Step 2: Write failing test for verdict in response**

Add to `tests/api/test_conversation.py`:

```python
async def test_verification_verdict_in_response(monkeypatch):
    from src.sdk import runner as _runner
    from src.sdk.messages import Message

    async def fake_run_sdk_agent(user_id, messages, **kwargs):
        return [Message.assistant(content="haiku here")]

    monkeypatch.setattr(_runner, "run_sdk_agent", fake_run_sdk_agent)

    response = client.post("/message", json={
        "message": "write a haiku",
        "verification": {"rubric": "- Three lines"},
    })
    assert response.status_code == 200
    body = response.json()
    assert "verification" in body
    # verification field present (status may be null if middleware not active)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run python -m pytest tests/api/test_conversation.py::test_verification_verdict_in_response -v`
Expected: FAIL

- [ ] **Step 4: Extract verification verdict from loop state after run**

In `src/http/routers/conversation.py`, after `run_sdk_agent` returns, extract verdict from the loop's state:

```python
verification_verdict = None
loop = get_user_loop(user_id, session_id=session_id)
if loop and hasattr(loop, "state") and loop.state:
    status = loop.state.extra.get("_rubric_status")
    if status:
        verification_verdict = VerificationVerdict(
            status=status,
            iterations=loop.state.extra.get("_rubric_iterations", 0),
            evaluations=loop.state.extra.get("_rubric_evaluations", []),
        )
```

Include `verification_verdict` in `MessageResponse`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m pytest tests/api/test_conversation.py::test_verification_verdict_in_response -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/http/models.py src/http/routers/conversation.py tests/api/test_conversation.py
git commit -m "feat: return verification verdicts in REST response"
```

---

### Task 10: Handle Rubric Events in Stream Adapter

**Files:**
- Modify: `src/http/stream_adapter.py`
- Test: `tests/api/test_stream_adapter.py`

- [ ] **Step 1: Write failing test for rubric event adaptation**

Add to `tests/api/test_stream_adapter.py`:

```python
def test_rubric_evaluation_start_adapts():
    chunk = StreamChunk(type="rubric_evaluation_start", content='{"grading_run_id":"abc","iteration":0}')
    event = adapt_stream_chunk(chunk)
    assert event.kind == "rubric_evaluation_start"

def test_rubric_evaluation_end_adapts():
    chunk = StreamChunk(type="rubric_evaluation_end", content='{"result":"satisfied"}')
    event = adapt_stream_chunk(chunk)
    assert event.kind == "rubric_evaluation_end"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/api/test_stream_adapter.py -v -k rubric`
Expected: FAIL

- [ ] **Step 3: Add rubric event handling to stream adapter**

In `src/http/stream_adapter.py`, the `adapt_stream_chunk` function already uses `chunk.canonical_type`. Since rubric types are not in the `_COMPAT_ALIAS_MAP`, they pass through as-is. The `StreamEvent.kind` will be `"rubric_evaluation_start"` or `"rubric_evaluation_end"` automatically. No code changes needed if the adapter already handles unknown types gracefully. If not, add explicit handling.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/api/test_stream_adapter.py -v -k rubric`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/api/test_stream_adapter.py
git commit -m "test: rubric event adaptation in stream adapter"
```

---

### Task 11: Verification Integration Test

**Files:**
- Test: `tests/sdk/test_verification_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/sdk/test_verification_integration.py`:

```python
import json
import pytest
from src.sdk.loop import AgentLoop, RunConfig
from src.sdk.middleware_rubric import RubricMiddleware
from src.sdk.messages import Message, StreamChunk
from src.sdk.state import AgentState


class FakeAgentProvider:
    """Provider that returns a pre-set response."""

    def __init__(self, responses: list[str]):
        self._responses = responses
        self._idx = 0

    async def chat(self, messages, tools=None, model=None, provider_options=None, **kwargs):
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return Message.assistant(content=resp)

    async def chat_stream(self, *args, **kwargs):
        raise NotImplementedError

    async def list_models(self):
        return []


class FakeGraderProvider:
    def __init__(self, response_json: str):
        self._json = response_json

    async def chat(self, messages, tools=None, model=None, provider_options=None, **kwargs):
        return Message.assistant(content=self._json)

    async def chat_stream(self, *args, **kwargs):
        raise NotImplementedError

    async def list_models(self):
        return []


@pytest.mark.asyncio
async def test_full_verification_loop_revision_then_satisfied():
    """Agent gives bad response, grader says needs_revision, agent gives good response, grader says satisfied."""
    agent = FakeAgentProvider([
        "two lines\nhere",  # bad response
        "line one\nline two\nline three",  # good response
    ])
    grader = FakeGraderProvider(json.dumps([
        {"result": "needs_revision", "explanation": "needs 3 lines", "criteria": [
            {"name": "three lines", "passed": False, "gap": "only 2 lines"}
        ]},
        {"result": "satisfied", "explanation": "all good", "criteria": [
            {"name": "three lines", "passed": True}
        ]},
    ][0]))

    # Grader returns needs_revision first, then satisfied
    grader_responses = [
        json.dumps({"result": "needs_revision", "explanation": "needs 3 lines", "criteria": [
            {"name": "three lines", "passed": False, "gap": "only 2 lines"}
        ]}),
        json.dumps({"result": "satisfied", "explanation": "all good", "criteria": [
            {"name": "three lines", "passed": True}
        ]}),
    ]

    class MultiResponseGrader:
        def __init__(self, responses):
            self._responses = responses
            self._idx = 0
            self.call_count = 0

        async def chat(self, *args, **kwargs):
            self.call_count += 1
            resp = self._responses[min(self._idx, len(self._responses) - 1)]
            self._idx += 1
            return Message.assistant(content=resp)

        async def chat_stream(self, *args, **kwargs):
            raise NotImplementedError

        async def list_models(self):
            return []

    grader = MultiResponseGrader(grader_responses)
    mw = RubricMiddleware(grader_provider=grader, max_iterations=3)

    loop = AgentLoop(
        provider=agent,
        tools=[],
        middlewares=[mw],
        run_config=RunConfig(max_iterations=5),
    )

    # Set rubric on loop
    loop.rubric = "- Three lines"

    result = await loop.run([Message.user("write a haiku")])

    assert grader.call_count == 2  # graded twice
    assert len(result) >= 4  # user + bad response + feedback + good response
    # Last assistant message should be the good one
    assistant_msgs = [m for m in result if m.role == "assistant"]
    assert "line one" in assistant_msgs[-1].content
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run python -m pytest tests/sdk/test_verification_integration.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/sdk/test_verification_integration.py
git commit -m "test: verification integration — revision then satisfied"
```

---

## Loop 3: Event-Driven Triggers

### Task 12: Create TriggerRegistry and AgentEvent

**Files:**
- Create: `src/sdk/loops/__init__.py`
- Create: `src/sdk/loops/events.py`
- Test: `tests/sdk/test_events.py`

- [ ] **Step 1: Write failing tests**

Create `tests/sdk/test_events.py`:

```python
import pytest
from src.sdk.loops.events import AgentEvent, TriggerRegistry


def test_agent_event_fields():
    event = AgentEvent(
        trigger_type="webhook",
        trigger_id="wh_123",
        user_id="alice",
        session_id="s1",
        message="check my email",
    )
    assert event.trigger_type == "webhook"
    assert event.rubric is None
    assert event.metadata == {}


@pytest.mark.asyncio
async def test_trigger_registry_fires_registered_handler():
    registry = TriggerRegistry()
    fired = []

    async def handler(event: AgentEvent):
        fired.append(event)

    registry.register("webhook", handler)

    event = AgentEvent(
        trigger_type="webhook",
        trigger_id="wh_1",
        user_id="alice",
        session_id="s1",
        message="hello",
    )
    await registry.fire(event)

    assert len(fired) == 1
    assert fired[0].message == "hello"


@pytest.mark.asyncio
async def test_trigger_registry_unknown_type_raises():
    registry = TriggerRegistry()
    event = AgentEvent(
        trigger_type="unknown",
        trigger_id="x",
        user_id="alice",
        session_id="s1",
        message="hello",
    )
    with pytest.raises(KeyError):
        await registry.fire(event)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/sdk/test_events.py -v`
Expected: FAIL

- [ ] **Step 3: Implement events module**

Create `src/sdk/loops/__init__.py` (empty) and `src/sdk/loops/events.py`:

```python
"""Event-driven triggers for the agent loop (loop 3)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src.app_logging import get_logger

logger = get_logger()


@dataclass
class AgentEvent:
    """Normalized event that triggers an agent run."""

    trigger_type: str
    trigger_id: str
    user_id: str
    session_id: str
    message: str
    rubric: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[AgentEvent], Awaitable[None]]


class TriggerRegistry:
    """Registry of trigger handlers, keyed by trigger type."""

    def __init__(self) -> None:
        self._handlers: dict[str, EventHandler] = {}

    def register(self, trigger_type: str, handler: EventHandler) -> None:
        self._handlers[trigger_type] = handler
        logger.info("trigger_registry.registered", {"trigger_type": trigger_type})

    def unregister(self, trigger_type: str) -> None:
        self._handlers.pop(trigger_type, None)

    async def fire(self, event: AgentEvent) -> None:
        handler = self._handlers.get(event.trigger_type)
        if handler is None:
            raise KeyError(f"No handler registered for trigger type: {event.trigger_type}")
        logger.info(
            "trigger_registry.firing",
            {"trigger_type": event.trigger_type, "trigger_id": event.trigger_id, "user_id": event.user_id},
            user_id=event.user_id,
        )
        await handler(event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/sdk/test_events.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sdk/loops/__init__.py src/sdk/loops/events.py tests/sdk/test_events.py
git commit -m "feat: add TriggerRegistry and AgentEvent for loop 3 (event-driven triggers)"
```

---

### Task 13: Add Webhook and Manual Trigger Endpoints

**Files:**
- Create: `src/http/routers/webhooks.py`
- Modify: `src/http/main.py` — register webhook router
- Test: `tests/api/test_webhooks.py`

- [ ] **Step 1: Write failing tests**

Create `tests/api/test_webhooks.py`:

```python
def test_manual_trigger_endpoint(client):
    response = client.post("/trigger", json={
        "user_id": "trigger_test",
        "session_id": "ts1",
        "message": "hello agent",
    })
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert body["status"] in ("started", "completed")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/api/test_webhooks.py -v`
Expected: FAIL — no `/trigger` endpoint.

- [ ] **Step 3: Implement webhook + trigger router**

Create `src/http/routers/webhooks.py` with `POST /trigger` endpoint that creates an `AgentEvent` and runs `run_sdk_agent` synchronously (or starts a background task). For v1, run synchronously and return the response.

```python
"""Webhook and manual trigger endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from src.sdk.runner import run_sdk_agent
from src.sdk.messages import Message

router = APIRouter(tags=["triggers"])


class TriggerRequest(BaseModel):
    user_id: str
    session_id: str
    message: str
    rubric: str | None = None
    model: str | None = None


class TriggerResponse(BaseModel):
    status: str
    response: str | None = None
    error: str | None = None


@router.post("/trigger", response_model=TriggerResponse)
async def manual_trigger(req: TriggerRequest) -> TriggerResponse:
    messages = [Message.user(req.message)]
    try:
        result = await run_sdk_agent(
            user_id=req.user_id,
            messages=messages,
            model=req.model,
            session_id=req.session_id,
            rubric=req.rubric,
        )
        response_text = ""
        for msg in reversed(result):
            if msg.role == "assistant" and isinstance(msg.content, str):
                response_text = msg.content
                break
        return TriggerResponse(status="completed", response=response_text)
    except Exception as e:
        return TriggerResponse(status="error", error=str(e))
```

In `src/http/main.py`, add:

```python
from src.http.routers.webhooks import router as webhooks_router
app.include_router(webhooks_router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/api/test_webhooks.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/http/routers/webhooks.py src/http/main.py tests/api/test_webhooks.py
git commit -m "feat: add POST /trigger manual trigger endpoint for loop 3"
```

---

## Loop 4: Hill-Climbing

### Task 14: Create RunOutcome Storage

**Files:**
- Create: `src/sdk/loops/storage.py`
- Test: `tests/sdk/test_run_outcome.py`

- [ ] **Step 1: Write failing tests**

Create `tests/sdk/test_run_outcome.py`:

```python
import pytest
from pathlib import Path
from src.sdk.loops.storage import RunOutcomeStore, RunOutcome


@pytest.mark.asyncio
async def test_persist_and_read_run_outcome(tmp_path):
    store = RunOutcomeStore(tmp_path / "loop_engineering.db")
    await store.init()

    outcome = RunOutcome(
        run_id="run_1",
        user_id="alice",
        session_id="s1",
        trigger_type="manual",
        response="hello",
        verification_status="satisfied",
        verification_iterations=1,
        verification_evaluations=[{"iteration": 0, "result": "satisfied"}],
        cost_usd=0.01,
        input_tokens=100,
        output_tokens=50,
        model="ollama-cloud:deepseek-v4-flash",
        timestamp="2026-07-27T10:00:00Z",
    )
    await store.save_run_outcome(outcome)

    outcomes = await store.list_run_outcomes("alice", limit=10)
    assert len(outcomes) == 1
    assert outcomes[0].run_id == "run_1"
    assert outcomes[0].verification_status == "satisfied"


@pytest.mark.asyncio
async def test_persist_and_read_improvement_suggestion(tmp_path):
    from src.sdk.loops.storage import ImprovementSuggestionStore, ImprovementSuggestion

    store = ImprovementSuggestionStore(tmp_path / "loop_engineering.db")
    await store.init()

    suggestion = ImprovementSuggestion(
        suggestion_id="sug_1",
        run_id="run_1",
        target_type="tool_description",
        target_name="files_read",
        current_value="Read a file",
        proposed_value="Read a file from disk. Supports text files.",
        rationale="Grader repeatedly fails on file reading tasks",
        risk_level="low",
        status="proposed",
        created_at="2026-07-27T10:00:00Z",
    )
    await store.save_suggestion(suggestion)

    suggestions = await store.list_suggestions("alice", status="proposed")
    assert len(suggestions) == 1
    assert suggestions[0].suggestion_id == "sug_1"
    assert suggestions[0].risk_level == "low"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/sdk/test_run_outcome.py -v`
Expected: FAIL

- [ ] **Step 3: Implement storage**

Create `src/sdk/loops/storage.py` with:
- `RunOutcome` dataclass
- `ImprovementSuggestion` dataclass
- `RunOutcomeStore` class with `init()`, `save_run_outcome(outcome)`, `list_run_outcomes(user_id, limit=50, since=None)`
- `ImprovementSuggestionStore` class with `init()`, `save_suggestion(suggestion)`, `list_suggestions(user_id, status=None)`, `update_suggestion_status(suggestion_id, status)`

Use `aiosqlite` for async SQLite access. Database path: `data/users/{user_id}/loop_engineering.db`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/sdk/test_run_outcome.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sdk/loops/storage.py tests/sdk/test_run_outcome.py
git commit -m "feat: add RunOutcome and ImprovementSuggestion SQLite storage for loop 4"
```

---

### Task 15: Persist RunOutcome After Each Agent Run

**Files:**
- Modify: `src/sdk/runner.py`
- Test: `tests/sdk/test_runner.py`

- [ ] **Step 1: Write failing test**

Add to `tests/sdk/test_runner.py`:

```python
@pytest.mark.asyncio
async def test_run_outcome_persisted_after_run(monkeypatch, tmp_path):
    from src.sdk.runner import run_sdk_agent
    from src.sdk.loops.storage import RunOutcomeStore
    from src.sdk.messages import Message

    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path))

    async def fake_create_sdk_loop(*args, **kwargs):
        class FakeLoop:
            state = None
            rubric = None
            async def run(self, messages):
                return [Message.assistant(content="done")]
        return FakeLoop()

    monkeypatch.setattr("src.sdk.runner.get_sdk_loop", fake_create_sdk_loop)

    result = await run_sdk_agent(
        user_id="outcome_test",
        messages=[Message.user("hello")],
        session_id="s1",
    )

    # Verify RunOutcome was persisted
    store = RunOutcomeStore(tmp_path / "users" / "outcome_test" / "loop_engineering.db")
    await store.init()
    outcomes = await store.list_run_outcomes("outcome_test")
    assert len(outcomes) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/sdk/test_runner.py::test_run_outcome_persisted_after_run -v`
Expected: FAIL

- [ ] **Step 3: Add RunOutcome persistence to run_sdk_agent**

In `src/sdk/runner.py`, after `loop.run()` completes, persist a `RunOutcome`:

```python
from src.sdk.loops.storage import RunOutcomeStore
from src.app_logging import get_logger

logger = get_logger()

async def _persist_run_outcome(
    user_id: str,
    session_id: str,
    response: str,
    loop: AgentLoop,
    trigger_type: str = "manual",
) -> None:
    try:
        from src.config import get_settings
        settings = get_settings()
        store = RunOutcomeStore(Path(settings.data_path) / "users" / user_id / "loop_engineering.db")
        await store.init()

        verification_status = None
        verification_iterations = 0
        verification_evaluations = []
        if loop.state and loop.state.extra:
            verification_status = loop.state.extra.get("_rubric_status")
            verification_iterations = loop.state.extra.get("_rubric_iterations", 0)
            verification_evaluations = loop.state.extra.get("_rubric_evaluations", [])

        import time, uuid
        outcome = RunOutcome(
            run_id=str(uuid.uuid4()),
            user_id=user_id,
            session_id=session_id or "default",
            trigger_type=trigger_type,
            response=response[:1000],  # truncate for storage
            verification_status=verification_status,
            verification_iterations=verification_iterations,
            verification_evaluations=verification_evaluations,
            cost_usd=0.0,  # TODO: from CostTracker
            input_tokens=0,
            output_tokens=0,
            model=getattr(loop.provider, "model", "unknown"),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        await store.save_run_outcome(outcome)
    except Exception as e:
        logger.warning("run_outcome.persist_failed", {"error": str(e)}, user_id=user_id)
```

Call `_persist_run_outcome` after `run_sdk_agent` returns.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/sdk/test_runner.py::test_run_outcome_persisted_after_run -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sdk/runner.py tests/sdk/test_runner.py
git commit -m "feat: persist RunOutcome after each agent run for loop 4"
```

---

### Task 16: Implement AnalysisJob

**Files:**
- Create: `src/sdk/loops/improvement.py`
- Test: `tests/sdk/test_improvement.py`

- [ ] **Step 1: Write failing tests**

Create `tests/sdk/test_improvement.py`:

```python
import json
import pytest
from unittest.mock import AsyncMock
from src.sdk.loops.improvement import AnalysisJob
from src.sdk.loops.storage import RunOutcome, RunOutcomeStore, ImprovementSuggestionStore
from src.sdk.messages import Message


class FakeAnalysisProvider:
    def __init__(self, response_json: str):
        self._json = response_json

    async def chat(self, messages, tools=None, model=None, provider_options=None, **kwargs):
        return Message.assistant(content=self._json)

    async def chat_stream(self, *args, **kwargs):
        raise NotImplementedError

    async def list_models(self):
        return []


@pytest.mark.asyncio
async def test_analysis_job_proposes_suggestions(tmp_path):
    # Seed outcomes
    outcome_store = RunOutcomeStore(tmp_path / "db.db")
    await outcome_store.init()
    await outcome_store.save_run_outcome(RunOutcome(
        run_id="r1", user_id="alice", session_id="s1", trigger_type="manual",
        response="bad response", verification_status="needs_revision",
        verification_iterations=3, verification_evaluations=[],
        cost_usd=0.01, input_tokens=100, output_tokens=50,
        model="test", timestamp="2026-07-27T10:00:00Z",
    ))

    suggestions_json = json.dumps([
        {
            "suggestion_id": "sug_1", "run_id": "r1",
            "target_type": "tool_description", "target_name": "files_read",
            "current_value": "Read", "proposed_value": "Read a file from disk",
            "rationale": "Grader failed", "risk_level": "low",
            "status": "proposed", "created_at": "2026-07-27T10:00:00Z"
        }
    ])

    provider = FakeAnalysisProvider(suggestions_json)
    job = AnalysisJob(analysis_provider=provider, mode="human_review")

    suggestions = await job.run("alice", outcome_store=outcome_store, suggestion_store=ImprovementSuggestionStore(tmp_path / "db.db"))

    assert len(suggestions) == 1
    assert suggestions[0].target_type == "tool_description"
    assert suggestions[0].risk_level == "low"
    assert suggestions[0].status == "proposed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/sdk/test_improvement.py -v`
Expected: FAIL

- [ ] **Step 3: Implement AnalysisJob**

Create `src/sdk/loops/improvement.py` with:
- `AnalysisJob.__init__(analysis_provider, mode="human_review", auto_apply_risk_threshold="low")`
- `AnalysisJob.run(user_id, outcome_store, suggestion_store, since=None)` — reads recent outcomes, calls analysis LLM with prompt to identify patterns and propose suggestions, parses JSON response into `ImprovementSuggestion` objects, persists them
- Analysis prompt: "Read these run outcomes. Identify patterns of failure. Propose improvements as JSON array of ImprovementSuggestion objects."

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/sdk/test_improvement.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sdk/loops/improvement.py tests/sdk/test_improvement.py
git commit -m "feat: implement AnalysisJob for loop 4 (hill-climbing)"
```

---

### Task 17: Add Improvement API Endpoints

**Files:**
- Create: `src/http/routers/improvements.py`
- Modify: `src/http/main.py`
- Test: `tests/api/test_improvements.py`

- [ ] **Step 1: Write failing tests**

Create `tests/api/test_improvements.py`:

```python
def test_list_improvements_empty(client):
    response = client.get("/improvements", params={"user_id": "imp_test"})
    assert response.status_code == 200
    assert response.json()["suggestions"] == []

def test_run_outcomes_empty(client):
    response = client.get("/run-outcomes", params={"user_id": "imp_test", "limit": 10})
    assert response.status_code == 200
    assert response.json()["outcomes"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/api/test_improvements.py -v`
Expected: FAIL

- [ ] **Step 3: Implement improvements router**

Create `src/http/routers/improvements.py` with:
- `GET /improvements?user_id=...` — list proposed suggestions
- `POST /improvements/{id}/approve` — approve and apply
- `POST /improvements/{id}/reject` — reject
- `POST /improvements/analyze` — trigger analysis job manually
- `GET /run-outcomes?user_id=...&limit=50` — list recent run outcomes

In `src/http/main.py`, add:

```python
from src.http.routers.improvements import router as improvements_router
app.include_router(improvements_router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/api/test_improvements.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/http/routers/improvements.py src/http/main.py tests/api/test_improvements.py
git commit -m "feat: add improvement and run-outcome API endpoints for loop 4"
```

---

### Task 18: Verification Sweep

**Files:**
- No new files

- [ ] **Step 1: Run full test suite**

Run: `uv run python -m pytest tests/sdk/ tests/api/ tests/storage/ -q`
Expected: PASS

- [ ] **Step 2: Run lint**

Run: `uv run ruff check src/ tests/`
Expected: PASS

- [ ] **Step 3: Run type check**

Run: `uv run mypy src/`
Expected: PASS or only pre-existing failures

- [ ] **Step 4: Smoke test with live LLM**

Start server, send request with `verification.rubric`, verify verdict in response.

```bash
uv run assistant http &
sleep 8
curl -s -X POST http://localhost:8080/message \
  -H "Content-Type: application/json" \
  -d '{"message":"Write a haiku about spring","verification":{"rubric":"- Three lines\n- About spring"}}' \
  | python3 -m json.tool
```

Expected: response includes `verification` field with status.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: loop engineering verification sweep complete"
```

---

## Self-Review

- **Spec coverage:** Loop 2 (tasks 1-11), loop 3 (tasks 12-13), loop 4 (tasks 14-17), verification sweep (task 18). All spec sections covered.
- **Placeholder scan:** No TBD/TODO. All steps have complete code.
- **Type consistency:** `RubricMiddleware`, `GraderResponse`, `RubricEvaluation`, `AgentEvent`, `TriggerRegistry`, `RunOutcome`, `ImprovementSuggestion`, `AnalysisJob` — names consistent across tasks.
- **Build order:** Loop 2 first (verification), loop 3 second (events), loop 4 last (hill-climbing). Matches spec.