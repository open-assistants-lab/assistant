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

import logging as stdlib_logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from src.app_logging import get_logger

logger = get_logger()


class _OtelDetachFilter(stdlib_logging.Filter):
    """Drop OTel's 'Failed to detach context' log noise.

    When a traced async generator is closed early (client disconnect, or the
    run loop breaking on an error chunk), GeneratorExit unwinds the OTel
    context managers and opentelemetry.context.detach() fails to reset a
    contextvar token that was created in a different asyncio context. OTel
    catches the ValueError itself and logs this traceback at ERROR level —
    the teardown is expected and the exception is already handled, so the
    log line is pure noise.
    """

    def filter(self, record: stdlib_logging.LogRecord) -> bool:
        return "Failed to detach context" not in record.getMessage()


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
            # OTel's detach() swallows the cross-context ValueError itself and
            # logs a full traceback; silence that expected teardown noise.
            stdlib_logging.getLogger("opentelemetry.context").addFilter(
                _OtelDetachFilter()
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
    def ensure_initialized(cls) -> None:
        """Initialize the client from settings if Langfuse is enabled.

        Idempotent. Called from trace_run/trace_span/wrap_provider so the
        client exists before the first run-level context opens (the loop
        wrapper inits it lazily, which is too late for RunService's
        trace_run on the first run).
        """
        if cls._client is not None:
            return
        try:
            from src.config import get_settings

            lf = get_settings().langfuse
            if lf.enabled and lf.public_key and lf.secret_key:
                cls.init(public_key=lf.public_key, secret_key=lf.secret_key, host=lf.host)
        except Exception:
            pass

    @classmethod
    @contextmanager
    def trace_run(cls, user_id: str, session_id: str) -> Iterator[Any]:
        """Open a run-level trace root.

        The loop's agent_run span and the rubric grader both nest under this
        root, so one run = one trace containing agent + grader observations.
        No-op (yields None) when Langfuse is disabled.
        """
        cls.ensure_initialized()
        client = cls._get_client()
        if client is None:
            yield None
            return
        from langfuse import propagate_attributes

        try:
            with client.start_as_current_observation(as_type="span", name="run") as trace:
                with propagate_attributes(
                    user_id=user_id,
                    session_id=session_id,
                    tags=["agent"],
                ):
                    try:
                        trace.update(input={"session_id": session_id})
                    except Exception:
                        pass
                    yield trace
        except ValueError as exc:
            # Suppress the OTel cross-context detach error on early teardown
            # (client disconnect); re-raise any other ValueError.
            if "was created in a different Context" in str(exc):
                return
            raise

    @classmethod
    @contextmanager
    def trace_span(cls, name: str) -> Iterator[Any]:
        """Open a named span under the current observation.

        Used for phases that run outside the loop's own spans (e.g. the
        rubric grader). No-op (yields None) when Langfuse is disabled.
        """
        cls.ensure_initialized()
        client = cls._get_client()
        if client is None:
            yield None
            return
        try:
            with client.start_as_current_observation(as_type="span", name=name) as span:
                yield span
        except ValueError as exc:
            if "was created in a different Context" in str(exc):
                return
            raise

    @classmethod
    def wrap_provider(cls, provider: Any) -> Any:
        """Wrap provider.chat() and chat_stream() with Langfuse generation spans."""
        if not cls.is_enabled():
            return provider

        original_chat = provider.chat
        original_chat_stream = provider.chat_stream
        provider_class = type(provider).__name__

        # Store originals so callers can bypass the wrapper to avoid double-tracing
        provider._original_chat = original_chat
        provider._original_chat_stream = original_chat_stream

        async def traced_chat(messages: list[Any], tools: Any = None, model: str | None = None, provider_options: Any = None, **kwargs: Any) -> Any:
            client = cls._get_client()
            if client is None:
                return await original_chat(
                    messages, tools=tools, model=model, provider_options=provider_options, **kwargs
                )

            model_name = model or getattr(provider, "model", "unknown")
            with client.start_as_current_observation(
                as_type="generation", name=f"{provider_class}_{model_name}", model=model_name
            ) as gen:
                try:
                    gen.update(
                        input=[m.model_dump() if hasattr(m, "model_dump") else str(m) for m in messages]
                    )
                except Exception:
                    pass
                response = await original_chat(
                    messages, tools=tools, model=model, provider_options=provider_options, **kwargs
                )
                try:
                    gen.update(
                        output=response.model_dump() if hasattr(response, "model_dump") else str(response)
                    )
                    if hasattr(response, "usage") and response.usage:
                        u = response.usage
                        gen.update(
                            usage_details={
                                "input": u.input_tokens,
                                "output": u.output_tokens,
                                "reasoning": u.reasoning_tokens,
                            }
                        )
                except Exception:
                    pass
                return response

        def traced_chat_stream(messages: list[Any], tools: Any = None, model: str | None = None, provider_options: Any = None, **kwargs: Any) -> Any:
            client = cls._get_client()
            if client is None:
                return original_chat_stream(
                    messages, tools=tools, model=model, provider_options=provider_options, **kwargs
                )

            model_name = model or getattr(provider, "model", "unknown")
            gen = client.start_observation(name=f"{provider_class}_{model_name}", as_type="generation")
            try:
                gen.update(
                    input=[m.model_dump() if hasattr(m, "model_dump") else str(m) for m in messages]
                )
            except Exception:
                pass

            async def wrapping_generator() -> AsyncIterator[Any]:
                accumulated_usage = {"input": 0, "output": 0, "reasoning": 0}
                text_parts: list[str] = []
                reasoning_parts: list[str] = []
                first_token_at: datetime | None = None
                try:
                    async for chunk in original_chat_stream(
                        messages, tools=tools, model=model, provider_options=provider_options, **kwargs
                    ):
                        # Count only the canonical event types — providers
                        # also emit backward-compat aliases (ai_token,
                        # reasoning) for the same content.
                        if chunk.type == "text_delta" and chunk.content:
                            text_parts.append(chunk.content)
                        elif chunk.type == "reasoning_delta" and chunk.content:
                            reasoning_parts.append(chunk.content)
                        elif chunk.type == "usage" and chunk.usage:
                            accumulated_usage["input"] += chunk.usage.input_tokens
                            accumulated_usage["output"] += chunk.usage.output_tokens
                            accumulated_usage["reasoning"] += chunk.usage.reasoning_tokens
                        if first_token_at is None and chunk.canonical_type in (
                            "text_delta",
                            "reasoning_delta",
                            "tool_input_delta",
                        ):
                            first_token_at = datetime.now(UTC)
                        yield chunk
                    try:
                        update_kwargs: dict[str, Any] = {"usage_details": accumulated_usage}
                        if first_token_at is not None:
                            # Langfuse derives timeToFirstToken from
                            # completion_start_time - startTime.
                            update_kwargs["completion_start_time"] = first_token_at
                        if text_parts or reasoning_parts:
                            update_kwargs["output"] = {
                                "role": "assistant",
                                "content": "".join(text_parts),
                                "reasoning": "".join(reasoning_parts) or None,
                            }
                        gen.update(**update_kwargs)
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

        async def traced_run(messages: list[Any]) -> Any:
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
                        trace.update(
                            input=[
                                m.model_dump() if hasattr(m, "model_dump") else str(m)
                                for m in messages[:5]
                            ]
                        )
                    except Exception:
                        pass
                    result = await original_run(messages)
                    if result:
                        last = result[-1]
                        if last.role == "assistant":
                            content = (
                                last.content if isinstance(last.content, str) else str(last.content)
                            )
                            try:
                                trace.update(output=content[:500])
                            except Exception:
                                pass
                    return result

        async def traced_run_stream(messages: list[Any]) -> AsyncIterator[Any]:
            client = cls._get_client()
            if client is None:
                async for chunk in original_run_stream(messages):
                    yield chunk
                return

            from langfuse import propagate_attributes

            # Use start_as_current_observation so child observations (LLM calls,
            # tool spans, middleware spans) are correctly nested under agent_run.
            #
            # Cross-context teardown robustness: both context managers below set
            # OpenTelemetry ContextVars internally. When this async generator is
            # torn down via aclose() from a different task (FastAPI
            # StreamingResponse client disconnect), GeneratorExit lands here and
            # the `with` __exit__ calls opentelemetry.context.detach(token),
            # which raises ValueError because the token was created in a
            # different contextvars.Context. We wrap the whole block in a
            # try/except that suppresses that ValueError so the SSE handler
            # never sees it; the trace is abandoned (correct — the stream was
            # cancelled) and the originating task's context is being discarded.
            try:
                with client.start_as_current_observation(as_type="span", name="agent_run") as trace:
                    with propagate_attributes(
                        user_id=user_id,
                        session_id=session_id,
                        tags=["agent"],
                    ):
                        try:
                            trace.update(
                                input=[
                                    m.model_dump() if hasattr(m, "model_dump") else str(m)
                                    for m in messages[:5]
                                ]
                            )
                        except Exception:
                            pass
                        async for chunk in original_run_stream(messages):
                            yield chunk
                        if loop.state and loop.state.messages:
                            last = loop.state.messages[-1]
                            if last.role == "assistant":
                                content = (
                                    last.content if isinstance(last.content, str) else str(last.content)
                                )
                                try:
                                    trace.update(output=content[:500])
                                except Exception:
                                    pass
            except ValueError as exc:
                # Suppress the OTel cross-context detach error on
                # GeneratorExit/aclose. Only suppress when it's the contextvar
                # token mismatch (the known cross-context teardown case);
                # re-raise any other ValueError.
                if "was created in a different Context" in str(exc):
                    return
                raise

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

            async def traced(tc: Any, state: Any) -> Any:
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
                            content = (
                                last_msg.content
                                if isinstance(last_msg.content, str)
                                else str(last_msg.content)
                            )
                            try:
                                span.update(
                                    output=content[:1000],
                                    metadata={"is_error": "error" in content.lower()},
                                )
                            except Exception:
                                pass

            loop._execute_single_tool = traced

        if hasattr(loop, "_execute_single_tool_streaming"):
            original_stream = loop._execute_single_tool_streaming

            async def traced_stream(tc: Any, state: Any) -> AsyncIterator[Any]:
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

            async def traced_batch(tool_calls: Any, state: Any) -> Any:
                with client.start_as_current_observation(as_type="span", name="tool:batch") as span:
                    try:
                        span.update(
                            input={
                                "tool_count": len(tool_calls),
                                "tools": [tc.name for tc in tool_calls],
                            }
                        )
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

            async def traced_batch_stream(tool_calls: Any, state: Any) -> AsyncIterator[Any]:
                with client.start_as_current_observation(as_type="span", name="tool:batch") as span:
                    try:
                        span.update(
                            input={
                                "tool_count": len(tool_calls),
                                "tools": [tc.name for tc in tool_calls],
                            }
                        )
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

        from src.sdk.middleware import Middleware
        from src.sdk.state import AgentState

        async def traced_run_hooks(hook_name: str, state: AgentState) -> None:
            for mw in loop.middlewares:
                method = getattr(mw, hook_name, None)
                if method is None:
                    continue
                # Skip inherited no-op hooks from Middleware base class — only trace
                # hooks that the middleware actually overrides
                if getattr(method, "__func__", method) is getattr(Middleware, hook_name, None):
                    continue

                with client.start_as_current_observation(
                    as_type="span", name=f"middleware:{mw.name}.{hook_name}"
                ) as span:
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
                        logger.warning("langfuse.hook_error", {"hook": hook_name, "middleware": mw.name, "error": str(e)})

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
            client.score_current_trace(
                name=name, value=value, data_type=data_type, comment=comment
            )
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
