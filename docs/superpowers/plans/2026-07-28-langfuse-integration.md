# Langfuse Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Langfuse v4 SDK for full traceability of LLM calls, tool executions, and middleware.

**Architecture:** A `LangfuseTracer` class wraps providers and agent loops at runtime via monkeypatching bound methods. Uses `start_as_current_observation` for sync paths and `start_observation` (manual lifecycle) for async generators. Existing `TraceProvider` stays untouched.

**Tech Stack:** Python 3.11, langfuse>=4.0, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-07-28-langfuse-integration-design.md`

---

## File Structure

**Create:**
- `src/sdk/langfuse_tracer.py` — `LangfuseTracer` class
- `tests/sdk/test_langfuse_tracer.py` — unit tests

**Modify:**
- `src/config/settings.py` — add `LangfuseConfig`
- `src/sdk/providers/factory.py` — wrap provider when Langfuse enabled
- `src/sdk/runner.py` — wrap loop when Langfuse enabled, flush after runs
- `src/sdk/middleware_rubric.py` — send scores after grading
- `pyproject.toml` — add `langfuse>=4.0` dependency

---

### Task 1: Add langfuse Dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add langfuse to dependencies**

Run: `uv add langfuse>=4.0`

- [ ] **Step 2: Verify installation**

Run: `uv run python -c "import langfuse; print(langfuse.__version__)"`

Expected: version printed (>=4.0)

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add langfuse>=4.0 dependency"
```

---

### Task 2: Add LangfuseConfig to Settings

**Files:**
- Modify: `src/config/settings.py`
- Test: `tests/sdk/test_config.py` (or inline test)

- [ ] **Step 1: Write failing test**

Add to a test file:

```python
def test_langfuse_config_defaults():
    from src.config import get_settings
    s = get_settings()
    assert hasattr(s, "langfuse")
    assert s.langfuse.enabled is False
    assert s.langfuse.public_key == ""
    assert s.langfuse.secret_key == ""
    assert s.langfuse.host == "http://localhost:3000"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/sdk/test_config.py::test_langfuse_config_defaults -v`
Expected: FAIL

- [ ] **Step 3: Add LangfuseConfig**

In `src/config/settings.py`, after `HillClimbingConfig`, add:

```python
class LangfuseConfig(_BaseSettings):
    """Langfuse observability configuration."""

    enabled: bool = False
    public_key: str = ""
    secret_key: str = ""
    host: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_prefix="LANGFUSE_")
```

In `AppConfig`, add:

```python
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/sdk/test_config.py::test_langfuse_config_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config/settings.py tests/sdk/test_config.py
git commit -m "feat: add LangfuseConfig to settings"
```

---

### Task 3: Implement LangfuseTracer

**Files:**
- Create: `src/sdk/langfuse_tracer.py`
- Test: `tests/sdk/test_langfuse_tracer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/sdk/test_langfuse_tracer.py`:

```python
"""Unit tests for LangfuseTracer."""

import pytest
from src.sdk.langfuse_tracer import LangfuseTracer


def test_is_enabled_false_when_not_initialized():
    LangfuseTracer._client = None
    assert LangfuseTracer.is_enabled() is False


def test_score_current_trace_noop_when_disabled():
    LangfuseTracer._client = None
    # Should not raise
    LangfuseTracer.score_current_trace(name="test", value=1.0)


def test_flush_noop_when_disabled():
    LangfuseTracer._client = None
    # Should not raise
    LangfuseTracer.flush()


def test_wrap_provider_returns_provider_when_disabled():
    LangfuseTracer._client = None

    class FakeProvider:
        async def chat(self, messages, **kwargs):
            return type("M", (), {"role": "assistant", "content": "hi"})()

    original = FakeProvider()
    wrapped = LangfuseTracer.wrap_provider(original)
    # When disabled, should return original unchanged
    assert wrapped is original


def test_wrap_loop_returns_loop_when_disabled():
    LangfuseTracer._client = None

    class FakeLoop:
        async def run(self, messages):
            return []

    original = FakeLoop()
    wrapped = LangfuseTracer.wrap_loop(original, user_id="u", session_id="s")
    # When disabled, should return original unchanged
    assert wrapped is original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/sdk/test_langfuse_tracer.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement LangfuseTracer**

Create `src/sdk/langfuse_tracer.py`:

```python
"""Langfuse tracer — wraps runtime functions for Langfuse observability.

When enabled (via LANGFUSE_ENABLED=true + credentials), wraps:
- LLM provider chat()/chat_stream() as Langfuse generations
- AgentLoop run()/run_stream() as trace roots with user_id/session_id
- Tool execution as Langfuse spans
- Middleware hooks as Langfuse spans
- Rubric verdicts as Langfuse scores

When disabled, all methods are no-ops with zero overhead.
"""

from __future__ import annotations

from typing import Any

from src.app_logging import get_logger

logger = get_logger()


class LangfuseTracer:
    """Wraps runtime functions with Langfuse tracing."""

    _client: Any | None = None  # langfuse.Langfuse singleton

    @classmethod
    def init(cls, public_key: str, secret_key: str, host: str) -> None:
        """Initialize Langfuse client. Called once on startup."""
        try:
            from langfuse import Langfuse

            cls._client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                base_url=host,
            )
            logger.info("langfuse.initialized", {"host": host})
        except Exception as e:
            logger.warning("langfuse.init_failed", {"error": str(e)})
            cls._client = None

    @classmethod
    def is_enabled(cls) -> bool:
        """Check if Langfuse tracing is active."""
        return cls._client is not None

    @classmethod
    def _get_client(cls) -> Any | None:
        """Get the Langfuse client, or None if not enabled."""
        if cls._client is None:
            return None
        try:
            from langfuse import get_client
            return get_client()
        except Exception:
            return cls._client

    @classmethod
    def wrap_provider(cls, provider: Any) -> Any:
        """Wrap provider.chat() and chat_stream() with Langfuse generation spans."""
        if not cls.is_enabled():
            return provider

        original_chat = provider.chat
        original_chat_stream = provider.chat_stream
        provider_class = type(provider).__name__

        async def traced_chat(messages, tools=None, model=None, provider_options=None, **kwargs):
            client = cls._get_client()
            if client is None:
                return await original_chat(messages, tools=tools, model=model, provider_options=provider_options, **kwargs)

            model_name = model or getattr(provider, "model", "unknown")
            with client.start_as_current_observation(
                as_type="generation", name=f"{provider_class}_{model_name}", model=model_name
            ) as gen:
                try:
                    gen.update(input=[m.model_dump() if hasattr(m, "model_dump") else str(m) for m in messages])
                except Exception:
                    pass
                response = await original_chat(messages, tools=tools, model=model, provider_options=provider_options, **kwargs)
                try:
                    gen.update(output=response.model_dump() if hasattr(response, "model_dump") else str(response))
                    if hasattr(response, "usage") and response.usage:
                        u = response.usage
                        gen.update(usage_details={
                            "input": u.input_tokens,
                            "output": u.output_tokens,
                            "reasoning": u.reasoning_tokens,
                        })
                except Exception:
                    pass
                return response

        def traced_chat_stream(messages, tools=None, model=None, provider_options=None, **kwargs):
            client = cls._get_client()
            if client is None:
                return original_chat_stream(messages, tools=tools, model=model, provider_options=provider_options, **kwargs)

            model_name = model or getattr(provider, "model", "unknown")
            gen = client.start_observation(name=f"{provider_class}_{model_name}", as_type="generation")
            try:
                gen.update(input=[m.model_dump() if hasattr(m, "model_dump") else str(m) for m in messages])
            except Exception:
                pass

            async def wrapping_generator():
                accumulated_usage = {"input": 0, "output": 0, "reasoning": 0}
                try:
                    async for chunk in original_chat_stream(messages, tools=tools, model=model, provider_options=provider_options, **kwargs):
                        if chunk.type == "usage" and chunk.usage:
                            accumulated_usage["input"] += chunk.usage.input_tokens
                            accumulated_usage["output"] += chunk.usage.output_tokens
                            accumulated_usage["reasoning"] += chunk.usage.reasoning_tokens
                        yield chunk
                    try:
                        gen.update(usage_details=accumulated_usage)
                    except Exception:
                        pass
                finally:
                    gen.end()

            return wrapping_generator()

        provider.chat = traced_chat
        provider.chat_stream = traced_chat_stream
        return provider

    @classmethod
    def wrap_loop(cls, loop: Any, user_id: str, session_id: str) -> Any:
        """Wrap AgentLoop.run()/run_stream() with trace context + middleware/tool spans."""
        if not cls.is_enabled():
            return loop

        original_run = loop.run
        original_run_stream = loop.run_stream

        async def traced_run(messages):
            client = cls._get_client()
            if client is None:
                return await original_run(messages)

            from langfuse import propagate_attributes

            with client.start_as_current_observation(as_type="span", name="agent_run") as trace:
                with propagate_attributes(
                    user_id=user_id,
                    session_id=session_id,
                    tags=["agent"],
                ):
                    try:
                        trace.update(input=[m.model_dump() if hasattr(m, "model_dump") else str(m) for m in messages[:5]])
                    except Exception:
                        pass
                    result = await original_run(messages)
                    if result:
                        last = result[-1]
                        if last.role == "assistant":
                            content = last.content if isinstance(last.content, str) else str(last.content)
                            try:
                                trace.update(output=content[:500])
                            except Exception:
                                pass
                    return result

        async def traced_run_stream(messages):
            client = cls._get_client()
            if client is None:
                async for chunk in original_run_stream(messages):
                    yield chunk
                return

            span = client.start_observation(name="agent_run", as_type="span")
            try:
                span.update(input=[m.model_dump() if hasattr(m, "model_dump") else str(m) for m in messages[:5]])
            except Exception:
                pass

            try:
                from langfuse import propagate_attributes
                with propagate_attributes(user_id=user_id, session_id=session_id, tags=["agent"]):
                    async for chunk in original_run_stream(messages):
                        yield chunk
            finally:
                if loop.state and loop.state.messages:
                    last = loop.state.messages[-1]
                    if last.role == "assistant":
                        content = last.content if isinstance(last.content, str) else str(last.content)
                        try:
                            span.update(output=content[:500])
                        except Exception:
                            pass
                span.end()

        loop.run = traced_run
        loop.run_stream = traced_run_stream

        # Wrap tool execution
        cls._wrap_tool_execution(loop)
        # Wrap middleware hooks
        cls._wrap_middleware_hooks(loop)

        return loop

    @classmethod
    def _wrap_tool_execution(cls, loop: Any) -> None:
        """Wrap _execute_single_tool and _execute_tool_batch with spans."""
        client = cls._get_client()
        if client is None:
            return

        if hasattr(loop, "_execute_single_tool"):
            original = loop._execute_single_tool

            async def traced(tc, state):
                with client.start_as_current_observation(as_type="span", name=f"tool:{tc.name}") as span:
                    try:
                        span.update(input=tc.arguments)
                    except Exception:
                        pass
                    msg_count = len(state.messages)
                    await original(tc, state)
                    if len(state.messages) > msg_count:
                        last_msg = state.messages[-1]
                        if last_msg.role == "tool":
                            content = last_msg.content if isinstance(last_msg.content, str) else str(last_msg.content)
                            try:
                                span.update(output=content[:1000], metadata={"is_error": "error" in content.lower()})
                            except Exception:
                                pass

            loop._execute_single_tool = traced

        if hasattr(loop, "_execute_single_tool_streaming"):
            original_stream = loop._execute_single_tool_streaming

            async def traced_stream(tc, state):
                with client.start_as_current_observation(as_type="span", name=f"tool:{tc.name}") as span:
                    try:
                        span.update(input=tc.arguments)
                    except Exception:
                        pass
                    async for chunk in original_stream(tc, state):
                        yield chunk

            loop._execute_single_tool_streaming = traced_stream

        if hasattr(loop, "_execute_tool_batch"):
            original_batch = loop._execute_tool_batch

            async def traced_batch(tool_calls, state):
                with client.start_as_current_observation(as_type="span", name="tool:batch") as span:
                    try:
                        span.update(input={"tool_count": len(tool_calls), "tools": [tc.name for tc in tool_calls]})
                    except Exception:
                        pass
                    msg_count = len(state.messages)
                    await original_batch(tool_calls, state)
                    new_msgs = state.messages[msg_count:]
                    if new_msgs:
                        try:
                            span.update(output={"result_count": len(new_msgs)})
                        except Exception:
                            pass

            loop._execute_tool_batch = traced_batch

        if hasattr(loop, "_execute_tool_batch_streaming"):
            original_batch_stream = loop._execute_tool_batch_streaming

            async def traced_batch_stream(tool_calls, state):
                with client.start_as_current_observation(as_type="span", name="tool:batch") as span:
                    try:
                        span.update(input={"tool_count": len(tool_calls), "tools": [tc.name for tc in tool_calls]})
                    except Exception:
                        pass
                    async for chunk in original_batch_stream(tool_calls, state):
                        yield chunk

            loop._execute_tool_batch_streaming = traced_batch_stream

    @classmethod
    def _wrap_middleware_hooks(cls, loop: Any) -> None:
        """Wrap _run_hooks to create per-middleware spans."""
        client = cls._get_client()
        if client is None:
            return

        from src.sdk.state import AgentState

        original_run_hooks = loop._run_hooks

        async def traced_run_hooks(hook_name: str, state: AgentState) -> None:
            for mw in loop.middlewares:
                with client.start_as_current_observation(
                    as_type="span", name=f"middleware:{mw.name}.{hook_name}"
                ) as span:
                    method = getattr(mw, hook_name, None)
                    if method is None:
                        continue
                    try:
                        updates = await method(state)
                        loop._apply_updates(state, updates)
                        if updates:
                            try:
                                span.update(output={"updates": updates})
                            except Exception:
                                pass
                    except Exception as e:
                        try:
                            span.update(metadata={"error": str(e)})
                        except Exception:
                            pass
                        logger.warning(f"{hook_name} error in {mw.name}", exc_info=True)

        loop._run_hooks = traced_run_hooks

    @classmethod
    def score_current_trace(
        cls, name: str, value: float, data_type: str = "BOOLEAN", comment: str = ""
    ) -> None:
        """Attach a score to the current trace."""
        client = cls._get_client()
        if client is None:
            return
        try:
            client.score_current_trace(name=name, value=value, data_type=data_type, comment=comment)
        except Exception as e:
            logger.warning("langfuse.score_failed", {"error": str(e)})

    @classmethod
    def flush(cls) -> None:
        """Flush pending events to Langfuse."""
        client = cls._get_client()
        if client is None:
            return
        try:
            client.flush()
        except Exception:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/sdk/test_langfuse_tracer.py -v`
Expected: PASS

- [ ] **Step 5: Run lint**

Run: `uv run ruff check src/sdk/langfuse_tracer.py tests/sdk/test_langfuse_tracer.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sdk/langfuse_tracer.py tests/sdk/test_langfuse_tracer.py
git commit -m "feat: implement LangfuseTracer with provider/loop/tool/middleware wrapping"
```

---

### Task 4: Wire LangfuseTracer into Factory

**Files:**
- Modify: `src/sdk/providers/factory.py`
- Test: `tests/sdk/test_langfuse_tracer.py`

- [ ] **Step 1: Write failing test for factory wiring**

Add to `tests/sdk/test_langfuse_tracer.py`:

```python
def test_factory_wraps_provider_when_enabled(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

    import src.config.settings as _cfg
    _cfg._config = None

    from src.sdk.langfuse_tracer import LangfuseTracer
    LangfuseTracer._client = None

    from src.sdk.providers.factory import create_model_from_config
    provider = create_model_from_config("ollama-cloud:test-model", user_id="test")

    # Provider should still work (wrapping is transparent)
    assert provider is not None
    assert LangfuseTracer.is_enabled() is True

    LangfuseTracer._client = None
    _cfg._config = None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/sdk/test_langfuse_tracer.py::test_factory_wraps_provider_when_enabled -v`
Expected: FAIL — factory doesn't wrap with Langfuse

- [ ] **Step 3: Add Langfuse wrapping to factory**

`create_model_from_config()` has multiple return points. Restructure to a single return by assigning to a `provider` variable:

```python
def create_model_from_config(
    config_model: str | None = None,
    provider_keys: dict[str, str] | None = None,
    user_id: str = "default_user",
) -> LLMProvider:
    from src.config import get_settings

    settings = get_settings()
    model_str = config_model or _load_stored_default_model(user_id) or settings.agent.model

    provider_type, model_name = _parse_model_string(model_str)

    resolved_key = None
    if provider_keys:
        resolved_key = provider_keys.get(provider_type) or provider_keys.get(provider_type.lower(), "")
        if not resolved_key:
            resolved_key = None

    if not resolved_key:
        resolved_key = _load_stored_key(provider_type, user_id)

    registry_provider = create_provider_from_registry_model(model_str, api_key=resolved_key)
    if registry_provider is not None:
        if resolved_key and hasattr(registry_provider, '_api_key'):
            registry_provider._api_key = resolved_key
        elif resolved_key:
            provider_type = getattr(registry_provider, 'provider_type', 'openai-compatible')
            base_url = getattr(registry_provider, 'base_url', None)
            provider = create_provider(provider_type, model=model_name, api_key=resolved_key, base_url=base_url)
        else:
            provider = registry_provider
    else:
        provider = create_provider(provider_type, model=model_name, api_key=resolved_key)

    # Wrap with Langfuse if enabled
    lf_settings = get_settings()
    if (
        lf_settings.langfuse.enabled
        and lf_settings.langfuse.public_key
        and lf_settings.langfuse.secret_key
    ):
        from src.sdk.langfuse_tracer import LangfuseTracer
        if not LangfuseTracer.is_enabled():
            LangfuseTracer.init(
                public_key=lf_settings.langfuse.public_key,
                secret_key=lf_settings.langfuse.secret_key,
                host=lf_settings.langfuse.host,
            )
        if LangfuseTracer.is_enabled():
            provider = LangfuseTracer.wrap_provider(provider)

    return provider
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/sdk/test_langfuse_tracer.py::test_factory_wraps_provider_when_enabled -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sdk/providers/factory.py tests/sdk/test_langfuse_tracer.py
git commit -m "feat: wire LangfuseTracer into provider factory"
```

---

### Task 5: Wire LangfuseTracer into Runner

**Files:**
- Modify: `src/sdk/runner.py`
- Test: `tests/sdk/test_runner.py`

- [ ] **Step 1: Write failing test**

Add to `tests/sdk/test_runner.py`:

```python
async def test_runner_wraps_loop_with_langfuse(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

    import src.config.settings as _cfg
    _cfg._config = None

    from src.sdk.langfuse_tracer import LangfuseTracer
    LangfuseTracer._client = None

    wrap_calls = []
    original_wrap = LangfuseTracer.wrap_loop
    def tracking_wrap(loop, user_id, session_id):
        wrap_calls.append({"user_id": user_id, "session_id": session_id})
        return original_wrap(loop, user_id, session_id)

    monkeypatch.setattr(LangfuseTracer, "wrap_loop", tracking_wrap)

    from src.sdk.runner import create_sdk_loop
    loop = await create_sdk_loop("lf_test_user", model="ollama-cloud:test-model")

    assert LangfuseTracer.is_enabled() is True
    assert len(wrap_calls) == 1
    assert wrap_calls[0]["user_id"] == "lf_test_user"

    LangfuseTracer._client = None
    _cfg._config = None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/sdk/test_runner.py::test_runner_wraps_loop_with_langfuse -v`
Expected: FAIL

- [ ] **Step 3: Add Langfuse wrapping to create_sdk_loop**

In `src/sdk/runner.py`, after the `AgentLoop(...)` is created and middlewares are added, add:

```python
    # Wrap with Langfuse if enabled
    from src.config import get_settings
    lf_settings = get_settings()
    if lf_settings.langfuse.enabled and lf_settings.langfuse.public_key and lf_settings.langfuse.secret_key:
        from src.sdk.langfuse_tracer import LangfuseTracer
        if not LangfuseTracer.is_enabled():
            LangfuseTracer.init(
                public_key=lf_settings.langfuse.public_key,
                secret_key=lf_settings.langfuse.secret_key,
                host=lf_settings.langfuse.host,
            )
        if LangfuseTracer.is_enabled():
            loop = LangfuseTracer.wrap_loop(loop, user_id=user_id, session_id=session_id or "default")
```

- [ ] **Step 4: Add flush after runs**

In `run_sdk_agent()`, after `result = await loop.run(messages)`, add:

```python
    from src.sdk.langfuse_tracer import LangfuseTracer
    LangfuseTracer.flush()
```

In `run_sdk_agent_stream()`, in the `finally` block, add:

```python
    from src.sdk.langfuse_tracer import LangfuseTracer
    LangfuseTracer.flush()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m pytest tests/sdk/test_runner.py::test_runner_wraps_loop_with_langfuse -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sdk/runner.py tests/sdk/test_runner.py
git commit -m "feat: wire LangfuseTracer into runner + flush after runs"
```

---

### Task 6: Send Rubric Scores to Langfuse

**Files:**
- Modify: `src/sdk/middleware_rubric.py`
- Test: `tests/sdk/test_middleware_rubric.py`

- [ ] **Step 1: Write failing test**

Add to `tests/sdk/test_middleware_rubric.py`:

```python
@pytest.mark.asyncio
async def test_rubric_sends_score_to_langfuse_when_enabled(monkeypatch):
    from src.sdk.langfuse_tracer import LangfuseTracer

    score_calls = []

    def fake_score_current_trace(name, value, data_type="BOOLEAN", comment=""):
        score_calls.append({"name": name, "value": value, "data_type": data_type, "comment": comment})

    def fake_is_enabled():
        return True

    monkeypatch.setattr(LangfuseTracer, "is_enabled", fake_is_enabled)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/sdk/test_middleware_rubric.py::test_rubric_sends_score_to_langfuse_when_enabled -v`
Expected: FAIL

- [ ] **Step 3: Add score sending to RubricMiddleware**

In `src/sdk/middleware_rubric.py`, after building the evaluation and before the `_emit_end` call, add:

```python
        # Send score to Langfuse if enabled
        try:
            from src.sdk.langfuse_tracer import LangfuseTracer
            if LangfuseTracer.is_enabled():
                LangfuseTracer.score_current_trace(
                    name=f"rubric_{evaluation['result']}",
                    value=1.0 if evaluation["result"] == "satisfied" else 0.0,
                    data_type="BOOLEAN",
                    comment=evaluation["explanation"],
                )
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/sdk/test_middleware_rubric.py::test_rubric_sends_score_to_langfuse_when_enabled -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sdk/middleware_rubric.py tests/sdk/test_middleware_rubric.py
git commit -m "feat: send rubric verdicts as Langfuse scores"
```

---

### Task 7: Add Langfuse Credentials to .env

**Files:**
- Modify: `.env`

- [ ] **Step 1: Add Langfuse env vars**

Append to `.env`:

```
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-544fcd7d-656c-47b3-b9c0-8479efc45375
LANGFUSE_SECRET_KEY=sk-lf-1080e092-8cde-45bf-b9ad-03cf610c2f18
LANGFUSE_HOST=https://langfuse.gongchatea.com.au
```

- [ ] **Step 2: Verify .env is gitignored**

Run: `git check-ignore .env`
Expected: `.env` (confirms it's ignored)

---

### Task 8: Verification Sweep + Live Test

**Files:**
- No new files

- [ ] **Step 1: Run full test suite**

Run: `uv run python -m pytest tests/sdk/ tests/api/ tests/storage/ -q`
Expected: PASS

- [ ] **Step 2: Run lint**

Run: `uv run ruff check src/ tests/`
Expected: PASS

- [ ] **Step 3: Live test with Langfuse**

Start server and send a message:

```bash
uv run assistant http &
sleep 8
curl -s -X POST http://localhost:8080/message \
  -H "Content-Type: application/json" \
  -d '{"message":"Say hello","model":"ollama-cloud:deepseek-v4-flash","verification":{"rubric":"- Response is non-empty"}}' \
  | python3 -m json.tool
```

Expected: Response includes verification verdict. Check Langfuse dashboard at `https://langfuse.gongchatea.com.au` for trace with:
- Agent run span
- LLM generation span
- Rubric middleware span
- Grader LLM generation span (nested inside middleware)
- Rubric score on trace

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: langfuse integration verification sweep complete"
```

---

## Self-Review

- **Spec coverage:** Settings (task 2), LangfuseTracer (task 3), factory wiring (task 4), runner wiring (task 5), rubric scores (task 6), live test (task 8). All spec sections covered.
- **Placeholder scan:** No TBD/TODO. All steps have complete code.
- **Type consistency:** `LangfuseTracer` methods consistent across tasks: `init()`, `is_enabled()`, `wrap_provider()`, `wrap_loop()`, `score_current_trace()`, `flush()`.
- **Build order:** Settings → Tracer → Factory → Runner → Scores → Live test. Each builds on the previous.