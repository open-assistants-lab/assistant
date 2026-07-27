# Langfuse Integration Design Spec

> **Date:** 2026-07-28
> **Goal:** Integrate Langfuse SDK directly into the agent runtime for full traceability of LLM calls, tool executions, middleware (rubric verification), and guardrails.

---

## Overview

Wrap key runtime functions with Langfuse's `start_as_current_observation` API to capture full I/O — LLM messages, tool arguments/results, rubric verdicts as scores, and cost/usage data. Our existing `TraceProvider` stays untouched for console/JSON.

```
AgentLoop.run()              ← trace root (user_id + session_id)
  ├── provider.chat()        ← generation — full messages, model, tokens
  ├── _execute_tool()        ← span — tool name, args, result, error
  ├── _run_hooks()           ← span per middleware per hook
  │   ├── RubricMiddleware.aafter_agent
  │   │   └── grader.chat()  ← generation — grader LLM call
  │   ├── SummarizationMiddleware.abefore_model
  │   └── MemoryMiddleware.aafter_agent
  └── (guardrails run inside _run_react_loop, not separately wrapped in v1)
```

Activation: `LANGFUSE_ENABLED=true` in settings. When disabled, wrappers are no-ops — zero overhead, no SDK loaded.

---

## Settings

```python
class LangfuseConfig(_BaseSettings):
    enabled: bool = False
    public_key: str = ""
    secret_key: str = ""
    host: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_prefix="LANGFUSE_")
```

Added to `AppConfig` as `langfuse: LangfuseConfig`.

---

## Files

**Create:**
- `src/sdk/langfuse_tracer.py` — `LangfuseTracer` class with `wrap_provider()`, `wrap_loop()`, `score_current_trace()`, `flush()`
- `tests/sdk/test_langfuse_tracer.py` — unit tests

**Modify:**
- `src/config/settings.py` — add `LangfuseConfig`
- `src/sdk/providers/factory.py` — wrap provider with Langfuse when enabled
- `src/sdk/runner.py` — pass user_id/session_id to Langfuse context
- `src/sdk/middleware_rubric.py` — call `LangfuseTracer.score_current_trace()` after grading
- `pyproject.toml` — add `langfuse>=4.0` dependency (v4 rewrite released March 2026)

---

## Components

### LangfuseTracer

```python
class LangfuseTracer:
    """Wraps runtime functions with Langfuse tracing via start_as_current_observation."""

    _client: Any | None = None  # langfuse.Langfuse client

    @classmethod
    def init(cls, public_key: str, secret_key: str, host: str) -> None:
        """Initialize Langfuse client via get_client(). Called once on startup."""

    @classmethod
    def is_enabled(cls) -> bool:
        """Check if Langfuse tracing is active."""

    @classmethod
    def wrap_provider(cls, provider: LLMProvider) -> LLMProvider:
        """Wrap provider.chat() and chat_stream() with Langfuse generation spans."""

    @classmethod
    def wrap_loop(cls, loop: AgentLoop, user_id: str, session_id: str) -> AgentLoop:
        """Wrap AgentLoop.run()/run_stream() with trace context + middleware/tool spans."""

    @classmethod
    def score_current_trace(cls, name: str, value: float, data_type: str = "BOOLEAN", comment: str = "") -> None:
        """Attach a score to the current trace (used by RubricMiddleware).

        Uses langfuse.score_current_trace() internally.
        data_type defaults to BOOLEAN (1.0 = pass, 0.0 = fail).
        """

    @classmethod
    def flush(cls) -> None:
        """Flush pending events to Langfuse (sync)."""
```

### Provider wrapping

`wrap_provider()` returns a proxy object that delegates to the original provider but wraps `chat()` and `chat_stream()` with manual Langfuse span management (not `@observe` decorator, since we're wrapping at runtime not definition time).

- **chat()**: wrapper creates a Langfuse generation via `langfuse.start_as_current_observation(as_type="generation", name=..., model=...)`, calls the original `chat()`, then updates the generation with:
  - Input: `messages` converted to OpenAI-format dicts
  - Output: response `Message` converted to OpenAI-format dict
  - Usage: `generation.update(usage_details={"input": ..., "output": ..., "reasoning": ...})`
  - Name: `{provider_class}_{model}` (e.g. `OllamaCloud_deepseek-v4-flash`)

- **chat_stream()**: wrapper creates a Langfuse generation, iterates the original `chat_stream()` generator, accumulates chunks (text, tool calls, reasoning, usage), yields each chunk through, then updates the generation with accumulated output and `usage_details` via `generation.update()` when the generator exhausts.

### Tool execution wrapping

`_execute_single_tool()` and `_execute_tool_batch()` return `None` — they add results to `state.messages` internally. So the wrapper can't capture the result from the return value. Instead, it captures the tool call arguments as input, and reads the last tool result message from `state.messages` after execution:

```python
original_execute = loop._execute_single_tool
async def traced_execute(tc, state):
    with langfuse.start_as_current_observation(as_type="span", name=f"tool:{tc.name}") as span:
        span.update(input=tc.arguments)
        msg_count_before = len(state.messages)
        await original_execute(tc, state)
        # Read the tool result that was added to state
        if len(state.messages) > msg_count_before:
            last_msg = state.messages[-1]
            if last_msg.role == "tool":
                content = last_msg.content if isinstance(last_msg.content, str) else str(last_msg.content)
                span.update(output=content[:1000], metadata={"is_error": "error" in content.lower()})
loop._execute_single_tool = traced_execute
```

Same pattern for `_execute_tool_batch()`. For streaming variants (`_execute_single_tool_streaming`, `_execute_tool_batch_streaming`), which are async generators yielding `StreamChunk`, the wrapper creates a span, iterates the original generator yielding chunks through, and ends the span after exhaustion:

```python
original_stream = loop._execute_single_tool_streaming
async def traced_stream_execute(tc, state):
    with langfuse.start_as_current_observation(as_type="span", name=f"tool:{tc.name}") as span:
        span.update(input=tc.arguments)
        async for chunk in original_stream(tc, state):
            yield chunk
loop._execute_single_tool_streaming = traced_stream_execute
```

### Middleware tracing

All middleware hooks flow through a single chokepoint: `AgentLoop._run_hooks(hook_name, state)`. This method iterates `self.middlewares` and calls each one's hook. Wrapping it captures every middleware execution — `RubricMiddleware`, `SummarizationMiddleware`, `MemoryMiddleware`, `ObservationMiddleware`, and any future middleware — without touching individual middleware classes.

```python
original_run_hooks = loop._run_hooks
async def traced_run_hooks(hook_name: str, state: AgentState) -> None:
    for mw in loop.middlewares:
        with langfuse.start_as_current_observation(as_type="span", name=f"middleware:{mw.name}.{hook_name}") as span:
            method = getattr(mw, hook_name, None)
            if method is None:
                continue
            try:
                updates = await method(state)
                loop._apply_updates(state, updates)
                if updates:
                    span.update(output={"updates": updates})
            except SubagentCancelledError:
                raise
            except Exception as e:
                span.update(metadata={"error": str(e)})
                logger.warning(f"{hook_name} error in {mw.name}", exc_info=True)
loop._run_hooks = traced_run_hooks
```

This captures:
- **Span name**: `middleware:{ClassName}.{hook_name}` (e.g. `middleware:RubricMiddleware.aafter_agent`)
- **Duration**: how long each middleware hook took
- **Errors**: if a middleware hook raised
- **State updates**: what the middleware returned

For `RubricMiddleware` specifically, the grader's LLM call inside `aafter_agent` is also traced via the provider wrapper (since the grader uses its own `LLMProvider`). So the Langfuse trace tree shows:

```
trace: agent_run (user_id, session_id)
  ├── generation: OllamaCloud_deepseek-v4-flash (agent LLM call)
  ├── span: tool:time_get
  ├── span: middleware:RubricMiddleware.aafter_agent
  │   └── generation: OllamaCloud_deepseek-v4-flash (grader LLM call)
  ├── span: middleware:SummarizationMiddleware.abefore_model
  └── span: middleware:MemoryMiddleware.aafter_agent
```

This gives full visibility into middleware overhead and behavior alongside LLM calls and tool execution.

Note: The traced `_run_hooks` re-implements the iteration logic (it doesn't call the original `_run_hooks`). This is necessary to create per-middleware spans. If `_run_hooks` logic changes in the future, the traced version must be updated to match. The risk is low since `_run_hooks` rarely changes.

### Loop wrapping

`wrap_loop()` sets Langfuse trace context:
- `trace_user_id = user_id`
- `trace_session_id = session_id`
- `trace_metadata = {"model": model, "provider": provider_type}`
- `trace_tags = ["agent", provider_type]`

Implemented by wrapping `AgentLoop.run()` and `run_stream()`. For `run()` (async function), the sync `with` context manager works fine:

```python
original_run = loop.run
async def traced_run(messages):
    from langfuse import get_client, propagate_attributes
    langfuse = get_client()
    with langfuse.start_as_current_observation(as_type="span", name="agent_run") as trace:
        with propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            metadata={"model": getattr(loop.provider, "model", "unknown")},
            tags=["agent"],
        ):
            trace.update(input=[m.model_dump() for m in messages[:5]])  # first 5 messages as input
            result = await original_run(messages)
            if result:
                last = result[-1]
                if last.role == "assistant":
                    trace.update(output=last.content[:500] if isinstance(last.content, str) else str(last.content)[:500])
            return result
loop.run = traced_run
```

For `run_stream()` (async generator), the sync `with` context manager would exit before the generator is consumed. We use `start_observation()` (manual lifecycle) instead:

```python
original_run_stream = loop.run_stream
async def traced_run_stream(messages):
    from langfuse import get_client, propagate_attributes
    langfuse = get_client()
    span = langfuse.start_observation(name="agent_run", as_type="span")
    span.update(input=[m.model_dump() for m in messages[:5]])
    # propagate_attributes is a context manager — use it for attribute propagation
    # but span lifecycle is manual
    try:
        async for chunk in original_run_stream(messages):
            yield chunk
        # Read final output from loop state
        if loop.state and loop.state.messages:
            last = loop.state.messages[-1]
            if last.role == "assistant":
                span.update(output=last.content[:500] if isinstance(last.content, str) else str(last.content)[:500])
    finally:
        span.end()
loop.run_stream = traced_run_stream
```

Note: `propagate_attributes(user_id=, session_id=)` must be called within the span's active context. For the streaming case, we set trace attributes via `langfuse.score_current_trace()` or by using `propagate_attributes` as a context manager wrapping the generator body. The exact mechanism depends on whether `propagate_attributes` works outside a `start_as_current_observation` block — if not, we fall back to setting attributes on the root span directly.

### Rubric scores

`RubricMiddleware.aafter_agent()` calls `langfuse.score_current_trace()` after each grading iteration:

```python
if LangfuseTracer.is_enabled():
    langfuse = get_client()
    langfuse.score_current_trace(
        name=f"rubric_{evaluation['result']}",
        value=1.0 if evaluation["result"] == "satisfied" else 0.0,
        data_type="BOOLEAN",
        comment=evaluation["explanation"],
    )
```

Using `data_type="BOOLEAN"` (1.0 = pass, 0.0 = fail) so Langfuse displays it as a boolean score in the dashboard.

This sends scores like:
- `rubric_satisfied` = 1.0
- `rubric_needs_revision` = 0.0
- `rubric_max_iterations_reached` = 0.0
- `rubric_failed` = 0.0
- `rubric_grader_error` = 0.0

### Factory integration

In `create_model_from_config()`, after creating the provider:

```python
from src.config import get_settings
settings = get_settings()
if settings.langfuse.enabled and settings.langfuse.public_key and settings.langfuse.secret_key:
    from src.sdk.langfuse_tracer import LangfuseTracer
    if not LangfuseTracer.is_enabled():
        LangfuseTracer.init(
            public_key=settings.langfuse.public_key,
            secret_key=settings.langfuse.secret_key,
            host=settings.langfuse.host,
        )
    provider = LangfuseTracer.wrap_provider(provider)
```

### Span export filter

Langfuse v4 has a default span filter that only exports Langfuse SDK spans and GenAI/LLM spans. Our custom tool and middleware spans are created via `start_as_current_observation`, so they should be exported by default. If any spans are missing in the dashboard, initialize with:

```python
from langfuse import Langfuse
Langfuse(should_export_span=lambda span: True)
```

This exports all spans regardless of instrumentation scope.

### Runner integration

In `create_sdk_loop()`, after creating the loop:

```python
if LangfuseTracer.is_enabled():
    loop = LangfuseTracer.wrap_loop(loop, user_id=user_id, session_id=session_id or "default")
```

In `run_sdk_agent()` and `run_sdk_agent_stream()`, call `LangfuseTracer.flush()` after the run completes.

---

## Error handling

- **Langfuse SDK not installed**: `import langfuse` fails → caught, tracing disabled, warning logged.
- **Langfuse server unreachable**: Langfuse SDK handles retries internally. If it fails, events are dropped silently — no impact on agent runs.
- **Score attachment fails**: caught and logged, no impact on grading.
- **Provider wrapping fails**: falls back to unwrapped provider, warning logged.

---

## Backward compatibility

- When `LANGFUSE_ENABLED=false` (default), no Langfuse code runs, no SDK imported, zero overhead.
- Existing `TraceProvider` (console/JSON) continues to work independently.
- No changes to `AgentLoop` internals, `LLMProvider` ABC, or middleware base class.

---

## Testing

- Unit test: `LangfuseTracer.is_enabled()` returns False when not initialized
- Unit test: `wrap_provider()` returns a callable that delegates to original
- Unit test: `wrap_loop()` sets trace metadata
- Unit test: `score_current_trace()` is a no-op when disabled
- Unit test: `init()` initializes client, `is_enabled()` returns True
- Integration test: full agent run with Langfuse enabled, verify trace exists (requires running Langfuse)
- Test: disabled mode has zero overhead (no langfuse import)