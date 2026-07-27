"""Agent loop — the core ReAct while-loop for the SDK.

The loop is:
    1. Call LLM with conversation history + tools
    2. If response has no tool_calls → done, return final message
    3. Execute each tool call, append results to messages
    4. Go back to step 1

Middleware hooks run at defined points:
    before_agent → before_model → [LLM call] → after_model → [tool exec] → (repeat) → after_agent

Features:
    - Structured block streaming (text_start/delta/end, tool_input_start/delta/end, reasoning_*)
    - Guardrails (input, output, tool-level)
    - Handoffs (multi-agent delegation)
    - Structured tracing (spans for LLM calls, tool exec, guardrails, handoffs)
    - Auto-approval via ToolAnnotations (replaces interrupt_on)
    - Cost tracking via RunConfig
    - Backward-compatible: also emits ai_token, tool_start, tool_end, reasoning
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from src.sdk.guardrails import (
    GuardrailResult,
    GuardrailTripwire,
    InputGuardrail,
    OutputGuardrail,
    ToolGuardrail,
)
from src.sdk.handoffs import Handoff
from src.sdk.messages import Message, StreamChunk, ToolCall, Usage
from src.sdk.middleware import Middleware
from src.sdk.providers.base import LLMProvider, ModelCost, ProviderContextOverflowError
from src.sdk.state import AgentState
from src.sdk.subagent_context import SubagentCancelledError, SubagentContext
from src.sdk.subagent_models import TaskCancelledError
from src.sdk.tools import ToolDefinition, ToolRegistry, ToolResult
from src.sdk.tracing import SpanType, TraceProvider
from src.sdk.validation import repair_tool_call

logger = logging.getLogger(__name__)

_current_agent_loop: ContextVar[AgentLoop | None] = ContextVar("_current_agent_loop", default=None)


def get_current_agent_loop() -> AgentLoop | None:
    """Return the currently active AgentLoop, or None if not inside a run.

    Set at the start of run() and run_stream(), cleared on exit.
    Used by tools like summarize_session to access the active loop.
    """
    return _current_agent_loop.get()


def _last_user_message(messages: list[Message]) -> Message | None:
    """Return the last non-tool, non-system message from the user."""
    for msg in reversed(messages):
        if msg.role == "user":
            return msg
    return None

DEFAULT_MAX_ITERATIONS = 25
DEFAULT_MAX_LLM_CALLS = 50
DEFAULT_MAX_TOKENS_TOTAL = 1_000_000
DEFAULT_COST_LIMIT_USD = 10.0


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


class CostTracker:
    """Tracks token usage and estimated cost per invocation."""

    def __init__(self) -> None:
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_reasoning_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.llm_calls: int = 0

    def add_usage(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        cost: ModelCost | None = None,
    ) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_reasoning_tokens += reasoning_tokens
        self.llm_calls += 1
        if cost:
            self.total_cost_usd += (input_tokens / 1_000_000) * cost.input + (
                output_tokens / 1_000_000
            ) * cost.output
            if cost.reasoning and reasoning_tokens:
                self.total_cost_usd += (reasoning_tokens / 1_000_000) * cost.reasoning

    def exceeds_limits(self, config: RunConfig) -> str | None:
        if self.llm_calls >= config.max_llm_calls:
            return f"max_llm_calls ({config.max_llm_calls}) reached"
        if self.total_cost_usd >= config.cost_limit_usd:
            return f"cost_limit_usd (${config.cost_limit_usd}) exceeded"
        if (
            self.total_input_tokens + self.total_output_tokens + self.total_reasoning_tokens
        ) >= config.max_tokens_total:
            return f"max_tokens_total ({config.max_tokens_total}) exceeded"
        return None


class Interrupt(Exception):  # noqa: N818
    """Raised when a tool call requires human approval."""

    def __init__(self, tool_call: ToolCall, allowed_actions: list[str] | None = None):
        self.tool_call = tool_call
        self.allowed_actions = allowed_actions or ["approve", "reject", "edit"]
        super().__init__(f"Interrupt: tool call '{tool_call.name}' requires approval")


class AgentLoop:
    """ReAct agent loop that replaces LangChain's create_agent().

    Usage:
        loop = AgentLoop(
            provider=ollama_provider,
            tools=[time_get, files_list],
            system_prompt="You are a helpful assistant.",
            middlewares=[],
        )
        result = await loop.run(messages)
        # or
        async for chunk in loop.run_stream(messages):
            handle(chunk)
    """

    def __init__(
        self,
        provider: LLMProvider,
        tools: list[ToolDefinition] | None = None,
        system_prompt: str | None = None,
        middlewares: list[Middleware] | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        input_guardrails: list[InputGuardrail] | None = None,
        output_guardrails: list[OutputGuardrail] | None = None,
        tool_guardrails: list[ToolGuardrail] | None = None,
        handoffs: list[Handoff] | None = None,
        trace_provider: TraceProvider | None = None,
        run_config: RunConfig | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        self.provider = provider
        self.system_prompt = system_prompt
        self.middlewares = middlewares or []
        self.max_iterations = max_iterations
        self.input_guardrails = input_guardrails or []
        self.output_guardrails = output_guardrails or []
        self.tool_guardrails = tool_guardrails or []
        self.handoffs = handoffs or []
        self.trace_provider = trace_provider
        self.run_config = run_config or RunConfig(max_iterations=max_iterations)
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.subagent_ctx: SubagentContext | None = None
        self.cancel_event: asyncio.Event | None = cancel_event
        self.rubric: str | None = None

        self._registry = ToolRegistry()
        if tools:
            for t in tools:
                self._registry.register(t)

        self._tool_index: Any | None = None
        self._recently_used: set[str] = set()

        self._handoff_tool_names: set[str] = set()
        for h in self.handoffs:
            self._handoff_tool_names.add(h.tool_name)

        self._approved_tools: set[tuple[str, str]] = set()
        self._approved_tool_calls: set[tuple[str, str, str]] = set()
        self._approved_tool_names: set[str] = set()

    def _tool_args_key(self, tc: ToolCall) -> str:
        return json.dumps(tc.arguments or {}, sort_keys=True)

    def approve_tool_call(self, tc: ToolCall) -> None:
        self._approved_tool_calls.add((tc.id, tc.name, self._tool_args_key(tc)))
        self._approved_tools.add((tc.name, self._tool_args_key(tc)))

    def register_tool(self, tool_def: ToolDefinition) -> None:
        if self._registry.has(tool_def.name):
            self._registry.remove(tool_def.name)
        self._registry.register(tool_def)

    def unregister_tool(self, name: str) -> bool:
        return self._registry.remove(name)

    def _apply_updates(self, state: AgentState, updates: dict[str, Any] | None) -> None:
        if updates:
            state.update(updates)

    def _should_interrupt(self, tc: ToolCall) -> bool:
        tool_def = self._registry.get(tc.name)
        if tool_def and tool_def.annotations.destructive and not tool_def.annotations.read_only:
            call_key = (tc.id, tc.name, self._tool_args_key(tc))
            if call_key in self._approved_tool_calls:
                self._approved_tool_calls.discard(call_key)
                return False
            if tc.arguments:
                args_key = (tc.name, json.dumps(tc.arguments, sort_keys=True))
                if args_key in self._approved_tools:
                    self._approved_tools.discard(args_key)
                    return False
            return True
        return False

    def _is_parallel_safe(self, tc: ToolCall) -> bool:
        """Check if a tool call is safe to execute in parallel with others.

        A tool is parallel-safe if it is read-only OR it is non-destructive.
        Destructive tools must run sequentially after parallel-safe ones.
        Interrupts are handled separately (never executed, just reported).
        """
        tool_def = self._registry.get(tc.name)
        if tool_def is None:
            return True
        return tool_def.annotations.read_only or not tool_def.annotations.destructive

    def _classify_tool_calls(
        self, tool_calls: list[ToolCall]
    ) -> tuple[list[ToolCall], list[ToolCall], list[ToolCall]]:
        """Classify tool calls into parallel-safe, sequential, and interrupt groups.

        Returns:
            (parallel_safe, sequential, interrupts) — three lists.
            parallel_safe: read-only or non-destructive, can run concurrently
            sequential: destructive, must run one-at-a-time after parallel batch
            interrupts: need human approval, reported but not executed
        """
        parallel_safe: list[ToolCall] = []
        sequential: list[ToolCall] = []
        interrupts: list[ToolCall] = []

        for tc in tool_calls:
            if self._should_interrupt(tc):
                interrupts.append(tc)
            elif self._is_parallel_safe(tc):
                parallel_safe.append(tc)
            else:
                sequential.append(tc)

        return parallel_safe, sequential, interrupts

    async def _execute_tool(self, tc: ToolCall) -> ToolResult:
        """Execute a tool call, returning a ToolResult with structured content."""
        tool_def = self._registry.get(tc.name)
        if tool_def is None:
            result = await self._try_lazy_load(tc)
            if result is not None:
                return result
            return ToolResult(content=f"Unknown tool: {tc.name}", is_error=True)

        self._recently_used.add(tc.name)
        tc = self._with_runtime_context(tc)

        try:
            if tool_def._coroutine:
                result = await tool_def.ainvoke(tc.arguments)
            else:
                result = tool_def.invoke(tc.arguments)
            logger.info(
                f"sdk.tool_executed tool={tc.name} source={tool_def.function.__module__ if tool_def.function else 'unknown'}"
            )
            return ToolResult.from_raw(result)
        except Exception as e:
            logger.error(f"tool_execution_error tool={tc.name}: {e}")
            return ToolResult(content=str(e), is_error=True)

    async def _try_lazy_load(self, tc: ToolCall) -> ToolResult | None:
        """Try to lazy-load a tool from the index and reconstruct its function."""
        if self._tool_index is None:
            return None
        td = self._tool_index.get_definition(tc.name)
        if td is None:
            return None
        reconstruct = self._tool_index.get_reconstruct(tc.name)
        tool_type = self._tool_index.get_tool_type(tc.name) or "unknown"

        if tool_type == "native":
            # Native tools are in the global registry — look them up by name.
            from src.sdk.native_tools import get_native_tools as _get_native_tools

            for nt in _get_native_tools():
                if nt.name == tc.name:
                    td = nt
                    break
            else:
                return ToolResult(
                    content=f"Native tool '{tc.name}' not found in the global registry.",
                    is_error=True,
                )
        elif tool_type == "custom":
            from src.sdk.tool_index import _rebuild_custom_function
            td = _rebuild_custom_function(td, reconstruct)
        elif tool_type == "mcp":
            mcp_bridge = getattr(self, "_mcp_bridge", None)
            if mcp_bridge is None:
                return ToolResult(content="MCP bridge not available. Restart the session.", is_error=True)
            resolved = mcp_bridge.get_tool_definition(tc.name)
            if resolved is None:
                parts = tc.name.split("__", 2)
                server = parts[1] if len(parts) == 3 else "unknown"
                return ToolResult(
                    content=f"MCP server '{server}' not connected. Run mcp_reload() to reconnect.",
                    is_error=True,
                )
            td = resolved
        elif tool_type == "connector":
            connectkit_bridge = getattr(self, "_connectkit_bridge", None)
            if connectkit_bridge is None:
                return ToolResult(content="Connector bridge not available. Restart the session.", is_error=True)
            all_defs = connectkit_bridge.get_tool_definitions()
            found = None
            for d in all_defs:
                if d.get("name") == tc.name:
                    found = d
                    break
            if found is None:
                return ToolResult(content=f"Connector tool '{tc.name}' session expired. Reconnect the service and try again.", is_error=True)
            from src.sdk.runner import _connector_dicts_to_defs
            resolved_list = _connector_dicts_to_defs([found])
            if resolved_list:
                td = resolved_list[0]
            else:
                return ToolResult(content=f"Failed to load connector tool '{tc.name}'.", is_error=True)

        self._registry.register(td)
        self._recently_used.add(tc.name)
        tc = self._with_runtime_context(tc)

        try:
            if td._coroutine:
                result = await td.ainvoke(tc.arguments)
            else:
                result = td.invoke(tc.arguments)
            return ToolResult.from_raw(result)
        except Exception as e:
            return ToolResult(content=str(e), is_error=True)

    def _with_runtime_context(self, tc: ToolCall) -> ToolCall:
        tool_def = self._registry.get(tc.name)
        if tool_def is None or not self.user_id:
            return tc
        props = tool_def.parameters.get("properties", {})
        args = dict(tc.arguments)
        if "user_id" in props:
            args["user_id"] = self.user_id
        if "workspace_id" in props:
            args["workspace_id"] = getattr(self, "workspace_id", "personal")
        if args == tc.arguments:
            return tc
        return ToolCall(id=tc.id, name=tc.name, arguments=args)

    async def _execute_single_tool(self, tc: ToolCall, state: AgentState) -> None:
        """Execute a single tool call with guardrails, hooks, and middleware, add result to state."""
        try:
            await self._check_tool_guardrails(tc, "input", tc.arguments)
        except GuardrailTripwire as e:
            state.add_message(
                Message.tool_result(
                    tool_call_id=tc.id,
                    content=json.dumps({"error": f"Tool input blocked: {e.result.message}"}),
                    name=tc.name,
                )
            )
            return

        # PreToolUse hooks removed — hooks were never wired into production
        # and the shell-subprocess model is wrong for streaming.
        # Use middleware (_add_middleware) for tool interception instead.

        for mw in self.middlewares:
            try:
                tc.arguments = mw.wrap_tool_call(tc.name, tc.arguments)
            except Exception:
                logger.warning(f"wrap_tool_call error in {mw.name} for {tc.name}", exc_info=True)

        if self.trace_provider:
            async with self.trace_provider.start_span(SpanType.TOOL_EXECUTION, tc.name) as span:
                result = await self._execute_tool(tc)
                span.set_meta("result_length", len(result.content))
                span.set_meta("is_error", result.is_error)
        else:
            result = await self._execute_tool(tc)

        self._record_subagent_tool(tc)
        if (ctx := self.subagent_ctx) and ctx.on_progress:
            await ctx.on_progress(ctx._step, "executing", f"Called {tc.name}")

        result_content = result.content
        if result.is_error:
            result_content = json.dumps({"error": result_content})

        try:
            await self._check_tool_guardrails(tc, "output", result_content)
        except GuardrailTripwire as e:
            result_content = json.dumps({"error": f"Tool output blocked: {e.result.message}"})

        state.add_message(
            Message.tool_result(
                tool_call_id=tc.id,
                content=result_content,
                name=tc.name,
            )
        )

    async def _execute_tool_batch(self, tool_calls: list[ToolCall], state: AgentState) -> None:
        """Execute a batch of parallel-safe tool calls concurrently via asyncio.gather().

        Each tool is guarded and middlewared independently. Errors in one
        tool don't affect others. Results are added to state after all complete.
        """

        async def _run_one(tc: ToolCall) -> Message:
            try:
                await self._check_tool_guardrails(tc, "input", tc.arguments)
            except GuardrailTripwire as e:
                return Message.tool_result(
                    tool_call_id=tc.id,
                    content=json.dumps({"error": f"Tool input blocked: {e.result.message}"}),
                    name=tc.name,
                )

            tc_args = dict(tc.arguments)
            for mw in self.middlewares:
                try:
                    tc_args = mw.wrap_tool_call(tc.name, tc_args)
                except Exception:
                    logger.warning(f"wrap_tool_call error in {mw.name} for {tc.name}", exc_info=True)

            tc_with_args = ToolCall(id=tc.id, name=tc.name, arguments=tc_args)

            if self.trace_provider:
                async with self.trace_provider.start_span(SpanType.TOOL_EXECUTION, tc.name) as span:
                    result = await self._execute_tool(tc_with_args)
                    span.set_meta("result_length", len(result.content))
                    span.set_meta("is_error", result.is_error)
            else:
                result = await self._execute_tool(tc_with_args)

            self._record_subagent_tool(tc)
            if (ctx := self.subagent_ctx) and ctx.on_progress:
                await ctx.on_progress(ctx._step, "executing", f"Called {tc.name}")

            result_content = result.content
            if result.is_error:
                result_content = json.dumps({"error": result_content})

            try:
                await self._check_tool_guardrails(tc, "output", result_content)
            except GuardrailTripwire as e:
                result_content = json.dumps({"error": f"Tool output blocked: {e.result.message}"})

            return Message.tool_result(
                tool_call_id=tc.id,
                content=result_content,
                name=tc.name,
            )

        results = await asyncio.gather(*[_run_one(tc) for tc in tool_calls], return_exceptions=True)

        for i, result in enumerate(results):
            tc = tool_calls[i]
            if isinstance(result, Exception):
                logger.error(f"parallel_tool_error tool={tc.name}: {result}")
                state.add_message(
                    Message.tool_result(
                        tool_call_id=tc.id,
                        content=json.dumps({"error": f"Tool execution failed: {result}"}),
                        name=tc.name,
                    )
                )
            else:
                if isinstance(result, BaseException):
                    state.add_message(
                        Message.tool_result(
                            tool_call_id=tc.id,
                            content=json.dumps({"error": f"Tool execution failed: {result}"}),
                            name=tc.name,
                        )
                    )
                else:
                    state.add_message(result)

    async def _execute_single_tool_streaming(
        self, tc: ToolCall, state: AgentState
    ) -> AsyncIterator[StreamChunk]:
        """Execute a single tool call with streaming events."""
        # Emit tool_input_start so the frontend can create a tool bubble before the result arrives
        yield StreamChunk.tool_input_start(
            tool=tc.name, call_id=tc.id, args=tc.arguments
        )
        try:
            await self._check_tool_guardrails(tc, "input", tc.arguments)
        except GuardrailTripwire as e:
            blocked_result = json.dumps({"error": f"Tool input blocked: {e.result.message}"})
            state.add_message(
                Message.tool_result(tool_call_id=tc.id, content=blocked_result, name=tc.name)
            )
            yield StreamChunk.tool_result_event(
                tool=tc.name, call_id=tc.id,                 result_preview=blocked_result[:2000]
            )
            yield StreamChunk.tool_end(
                tool=tc.name, call_id=tc.id, result_preview=blocked_result[:2000]
            )
            return

        for mw in self.middlewares:
            try:
                tc.arguments = mw.wrap_tool_call(tc.name, tc.arguments)
            except Exception:
                logger.warning(f"wrap_tool_call error in {mw.name} for {tc.name}", exc_info=True)

        if self.trace_provider:
            async with self.trace_provider.start_span(
                SpanType.TOOL_EXECUTION, tc.name
            ) as tool_span:
                result = await self._execute_tool(tc)
                tool_span.set_meta("result_length", len(result.content))
                tool_span.set_meta("is_error", result.is_error)
        else:
            result = await self._execute_tool(tc)

        self._record_subagent_tool(tc)
        if (ctx := self.subagent_ctx) and ctx.on_progress:
            await ctx.on_progress(ctx._step, "executing", f"Called {tc.name}")

        result_content = result.content
        if result.is_error:
            result_content = json.dumps({"error": result_content})

        try:
            await self._check_tool_guardrails(tc, "output", result_content)
        except GuardrailTripwire as e:
            result_content = json.dumps({"error": f"Tool output blocked: {e.result.message}"})

        state.add_message(
            Message.tool_result(
                tool_call_id=tc.id,
                content=result_content,
                name=tc.name,
            )
        )
        preview = result_content[:2000] if result_content else ""
        yield StreamChunk.tool_result_event(tool=tc.name, call_id=tc.id, result_preview=preview)
        yield StreamChunk.tool_end(tool=tc.name, call_id=tc.id, result_preview=preview)

    async def _execute_tool_batch_streaming(
        self, tool_calls: list[ToolCall], state: AgentState
    ) -> AsyncIterator[StreamChunk]:
        """Execute a batch of parallel-safe tool calls concurrently, yielding events.

        Uses asyncio.gather for concurrent execution. Events are yielded
        after all tools complete to maintain message ordering in state.
        """
        # Emit tool_input_start for each tool so the frontend can create tool bubbles
        for tc in tool_calls:
            yield StreamChunk.tool_input_start(
                tool=tc.name, call_id=tc.id, args=tc.arguments
            )

        async def _run_one(tc: ToolCall) -> tuple[ToolCall, str]:
            try:
                await self._check_tool_guardrails(tc, "input", tc.arguments)
            except GuardrailTripwire as e:
                return tc, json.dumps({"error": f"Tool input blocked: {e.result.message}"})

            tc_args = dict(tc.arguments)
            for mw in self.middlewares:
                try:
                    tc_args = mw.wrap_tool_call(tc.name, tc_args)
                except Exception:
                    logger.warning(f"wrap_tool_call error in {mw.name} for {tc.name}", exc_info=True)

            tc_with_args = ToolCall(id=tc.id, name=tc.name, arguments=tc_args)

            if self.trace_provider:
                async with self.trace_provider.start_span(SpanType.TOOL_EXECUTION, tc.name) as span:
                    result = await self._execute_tool(tc_with_args)
                    span.set_meta("result_length", len(result.content))
                    span.set_meta("is_error", result.is_error)
            else:
                result = await self._execute_tool(tc_with_args)

            self._record_subagent_tool(tc)
            if (ctx := self.subagent_ctx) and ctx.on_progress:
                await ctx.on_progress(ctx._step, "executing", f"Called {tc.name}")

            result_content = result.content
            if result.is_error:
                result_content = json.dumps({"error": result_content})

            try:
                await self._check_tool_guardrails(tc, "output", result_content)
            except GuardrailTripwire as e:
                result_content = json.dumps({"error": f"Tool output blocked: {e.result.message}"})

            return tc, result_content

        results = await asyncio.gather(*[_run_one(tc) for tc in tool_calls], return_exceptions=True)

        for i, result in enumerate(results):
            tc = tool_calls[i]
            if isinstance(result, Exception):
                logger.error(f"parallel_tool_error tool={tc.name}: {result}")
                result_content = json.dumps({"error": f"Tool execution failed: {result}"})
            else:
                tc_r, result_content = result  # type: ignore[misc]

            state.add_message(
                Message.tool_result(
                    tool_call_id=tc.id,
                    content=result_content,
                    name=tc.name,
                )
            )
            preview = result_content[:500] if result_content else ""
            yield StreamChunk.tool_result_event(tool=tc.name, call_id=tc.id, result_preview=preview)
            yield StreamChunk.tool_end(tool=tc.name, call_id=tc.id, result_preview=preview)

    async def _run_hooks(self, hook_name: str, state: AgentState) -> None:
        for mw in self.middlewares:
            method = getattr(mw, hook_name, None)
            if method is None:
                continue
            try:
                updates = await method(state)
                self._apply_updates(state, updates)
            except TaskCancelledError:
                raise
            except Exception:
                logger.warning(f"{hook_name} error in {mw.name}", exc_info=True)

    def _prepare_messages(self, state: AgentState) -> list[Message]:
        messages = list(state.messages)
        if self.system_prompt:
            if not messages or messages[0].role != "system":
                messages.insert(0, Message.system(self.system_prompt))
            elif (
                isinstance(messages[0].content, str)
                and self.system_prompt not in messages[0].content
            ):
                messages[0] = Message.system(f"{self.system_prompt}\n\n{messages[0].content}")
        return messages

    async def _check_input_guardrails(self, state: AgentState) -> GuardrailResult | None:
        user_msgs = state.user_messages()
        if not user_msgs:
            return None
        last_input = user_msgs[-1].content
        if isinstance(last_input, list):
            last_input = str(last_input)

        for guardrail in self.input_guardrails:
            try:
                if self.trace_provider:
                    async with self.trace_provider.start_span(
                        SpanType.GUARDRAIL, guardrail.name
                    ) as span:
                        result = await guardrail.check(last_input, state)
                        span.set_meta("triggered", result.tripwire_triggered)
                else:
                    result = await guardrail.check(last_input, state)
                if result.tripwire_triggered:
                    raise GuardrailTripwire(result, guardrail.name)
            except GuardrailTripwire:
                raise
            except Exception as e:
                logger.warning(f"input_guardrail_error name={guardrail.name}: {e}")
        return None

    async def _check_output_guardrails(self, output: str, state: AgentState) -> None:
        for guardrail in self.output_guardrails:
            try:
                if self.trace_provider:
                    async with self.trace_provider.start_span(
                        SpanType.GUARDRAIL, guardrail.name
                    ) as span:
                        result = await guardrail.check(output, state)
                        span.set_meta("triggered", result.tripwire_triggered)
                else:
                    result = await guardrail.check(output, state)
                if result.tripwire_triggered:
                    raise GuardrailTripwire(result, guardrail.name)
            except GuardrailTripwire:
                raise
            except Exception as e:
                logger.warning(f"output_guardrail_error name={guardrail.name}: {e}")

    async def _check_tool_guardrails(
        self, tc: ToolCall, phase: str, data: dict[str, Any] | str
    ) -> GuardrailResult | None:
        for guardrail in self.tool_guardrails:
            try:
                if phase == "input" and guardrail.check_input:
                    result = await guardrail.check_input(tc.name, data)
                    if result and result.tripwire_triggered:
                        raise GuardrailTripwire(result, guardrail.name)
                elif phase == "output" and guardrail.check_output:
                    result = await guardrail.check_output(tc.name, str(data))
                    if result and result.tripwire_triggered:
                        raise GuardrailTripwire(result, guardrail.name)
            except GuardrailTripwire:
                raise
            except Exception as e:
                logger.warning(f"tool_guardrail_error name={guardrail.name}: {e}")
        return None

    def find_middleware(self, mw_type: type) -> Any | None:
        """Return the first middleware matching the given type, or None."""
        for mw in self.middlewares:
            if isinstance(mw, mw_type):
                return mw
        return None

    async def _check_subagent_before_llm(self, state: AgentState) -> None:
        if not (ctx := self.subagent_ctx):
            return
        if ctx.cancel_event.is_set():
            raise SubagentCancelledError(ctx._task_id)
        while not ctx.instructions.empty():
            msg = await ctx.instructions.get()
            state.add_message(Message.system(f"[Supervisor Update] {msg}"))
        if ctx.doom_detected:
            raise SubagentCancelledError(ctx._task_id, "doom loop detected")
        if ctx.on_progress:
            await ctx.on_progress(ctx._step, "thinking", "generating response")

    def _record_subagent_tool(self, tc: ToolCall) -> None:
        if not (ctx := self.subagent_ctx):
            return
        ctx.record_tool_call(tc.name, json.dumps(tc.arguments, sort_keys=True))

    async def run(self, messages: list[Message]) -> list[Message]:
        """Run the agent loop to completion. Returns final message list."""
        token = _current_agent_loop.set(self)
        try:
            return await self._run_impl(messages)
        finally:
            _current_agent_loop.reset(token)

    async def _run_impl(self, messages: list[Message]) -> list[Message]:
        """Internal run implementation (wrapped by run() for ContextVar lifecycle)."""
        state = AgentState(messages=list(messages))
        self.state = state
        if self.rubric:
            state.extra["rubric"] = self.rubric
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

    async def _run_react_loop(self, state: AgentState, cost_tracker: CostTracker) -> None:
        """Run the ReAct loop body — LLM calls and tool execution."""
        for iteration in range(self.run_config.max_iterations):
            limit_reason = cost_tracker.exceeds_limits(self.run_config)
            if limit_reason:
                state.add_message(Message.assistant(content=f"Run limit reached: {limit_reason}"))
                break

            overflow_retries = 0
            llm_success = False
            while overflow_retries < 3 and not llm_success:
                await self._check_subagent_before_llm(state)
                await self._run_hooks("abefore_model", state)

                prepared = self._prepare_messages(state)
                tools = self._registry.list_tools() or None

                try:
                    if self.trace_provider:
                        async with self.trace_provider.start_span(
                            SpanType.LLM_CALL, f"llm_call_{iteration}"
                        ) as span:
                            response = await self.provider.chat(
                                prepared,
                                tools=tools,
                                model=None,
                                provider_options=self.run_config.provider_options,
                            )
                            span.set_meta("has_tool_calls", bool(response.tool_calls))
                            if response.usage:
                                span.set_meta("input_tokens", response.usage.input_tokens)
                                span.set_meta("output_tokens", response.usage.output_tokens)
                            cost_tracker.add_usage(
                                input_tokens=response.usage.input_tokens if response.usage else 0,
                                output_tokens=response.usage.output_tokens if response.usage else 0,
                                reasoning_tokens=response.usage.reasoning_tokens
                                if response.usage
                                else 0,
                            )
                    else:
                        response = await self.provider.chat(
                            prepared,
                            tools=tools,
                            model=None,
                            provider_options=self.run_config.provider_options,
                        )
                        cost_tracker.add_usage(
                            input_tokens=response.usage.input_tokens if response.usage else 0,
                            output_tokens=response.usage.output_tokens if response.usage else 0,
                            reasoning_tokens=response.usage.reasoning_tokens if response.usage else 0,
                        )
                    llm_success = True
                except ProviderContextOverflowError:
                    overflow_retries += 1
                    logger.warning(f"context_overflow iteration={iteration} retry={overflow_retries}")

                    from src.sdk.middleware_summarization import SummarizationMiddleware

                    summary_mw = self.find_middleware(SummarizationMiddleware)
                    if summary_mw is not None:
                        success = await summary_mw.force_summarize(state)
                        if success:
                            continue

                    state.add_message(
                        Message.assistant(content="Context too large after summarization attempt.")
                    )
                    break

                except Exception as e:
                    logger.error(f"llm_error iteration={iteration}: {e}")
                    state.add_message(Message.assistant(content=f"Error: {e}"))
                    break

            if not llm_success:
                break

            state.add_message(response)

            await self._run_hooks("aafter_model", state)

            if not response.tool_calls:
                output_text = response.content if isinstance(response.content, str) else ""
                try:
                    await self._check_output_guardrails(output_text, state)
                except GuardrailTripwire as e:
                    state.add_message(
                        Message.assistant(content=f"Output blocked: {e.result.message}")
                    )
                break

            effective_tool_calls = [self._with_runtime_context(tc) for tc in response.tool_calls]

            # Classify tool calls: parallel-safe, sequential, interrupts
            parallel_safe, sequential, interrupts = self._classify_tool_calls(effective_tool_calls)

            # Handle interrupts: add interrupt tool_result messages (not executed)
            for tc in interrupts:
                state.add_message(
                    Message.tool_result(
                        tool_call_id=tc.id,
                        content=json.dumps(
                            {
                                "interrupt": True,
                                "tool": tc.name,
                                "args": tc.arguments,
                                "message": f"Tool call '{tc.name}' requires approval",
                                "allowed_actions": ["approve", "reject", "edit"],
                            }
                        ),
                        name=tc.name,
                    )
                )

            # Execute parallel-safe tools concurrently
            if parallel_safe:
                await self._execute_tool_batch(parallel_safe, state)

            # Execute sequential (destructive) tools one-at-a-time
            for tc in sequential:
                await self._execute_single_tool(tc, state)

    async def run_stream(self, messages: list[Message]) -> AsyncIterator[StreamChunk]:
        """Run the agent loop, yielding StreamChunk events in real-time.

        Emits block-structured events:
            text_start / text_delta / text_end
            tool_input_start / tool_input_delta / tool_input_end
            reasoning_start / reasoning_delta / reasoning_end
            tool_result (after tool execution)
            interrupt / done / error

        Also emits backward-compatible aliases:
            ai_token (alongside text_delta)
            tool_start (alongside tool_input_start)
            tool_end (alongside tool_result)
            reasoning (alongside reasoning_delta)
        """
        state = AgentState(messages=list(messages))
        self.state = state
        if self.rubric:
            state.extra["rubric"] = self.rubric
        cost_tracker = CostTracker()
        all_tool_calls: list[dict[str, Any]] = []

        token = _current_agent_loop.set(self)

        await self._run_hooks("abefore_agent", state)

        try:
            if self.trace_provider:
                async with self.trace_provider.start_span(SpanType.AGENT, "agent_run"):
                    async for chunk in self._run_stream_inner(state, cost_tracker, all_tool_calls):
                        yield chunk

            else:
                async for chunk in self._run_stream_inner(state, cost_tracker, all_tool_calls):
                    yield chunk
        finally:
            _current_agent_loop.reset(token)

    async def _run_stream_inner(
        self,
        state: AgentState,
        cost_tracker: CostTracker,
        all_tool_calls: list[dict[str, Any]],
    ) -> AsyncIterator[StreamChunk]:
        guardrail_task: asyncio.Task[GuardrailResult | None] | None = None
        try:
            guardrail_task = asyncio.ensure_future(self._check_input_guardrails(state))
        except Exception:
            guardrail_task = None

        overflow_retries = 0

        try:
            for iteration in range(self.run_config.max_iterations):
                # Cooperative cancellation check
                if self.cancel_event and self.cancel_event.is_set():
                    yield StreamChunk.done(content="", tool_calls=all_tool_calls)
                    return

                limit_reason = cost_tracker.exceeds_limits(self.run_config)
                if limit_reason:
                    yield StreamChunk.error(message=f"Run limit reached: {limit_reason}")
                    break

                await self._check_subagent_before_llm(state)
                await self._run_hooks("abefore_model", state)

                prepared = self._prepare_messages(state)
                tools = self._registry.list_tools() or None

                stream_content_parts: list[str] = []
                stream_tool_calls: list[ToolCall] = []
                stream_tool_calls_map: dict[int, dict[str, Any]] = {}
                stream_reasoning_parts: list[str] = []
                in_text_block = False
                in_reasoning_block = False
                stream_usage = Usage()

                if guardrail_task is not None:
                    try:
                        await guardrail_task
                    except GuardrailTripwire as e:
                        yield StreamChunk.error(message=f"Input blocked: {e.result.message}")
                        break
                    guardrail_task = None

                try:
                    if self.trace_provider:
                        async with self.trace_provider.start_span(
                            SpanType.LLM_CALL, f"llm_call_{iteration}"
                        ) as llm_span:
                            async for chunk in self.provider.chat_stream(
                                prepared,
                                tools=tools,
                                model=None,
                                provider_options=self.run_config.provider_options,
                            ):
                                # Cooperative cancellation during token streaming
                                if self.cancel_event and self.cancel_event.is_set():
                                    yield StreamChunk.done(content="", tool_calls=all_tool_calls)
                                    return
                                if chunk.type == "usage" and chunk.usage:
                                    stream_usage.input_tokens += chunk.usage.input_tokens
                                    stream_usage.output_tokens += chunk.usage.output_tokens
                                    stream_usage.reasoning_tokens += chunk.usage.reasoning_tokens
                                    stream_usage.cache_read_tokens += chunk.usage.cache_read_tokens
                                    stream_usage.cache_creation_tokens += (
                                        chunk.usage.cache_creation_tokens
                                    )
                                    continue
                                async for event in self._process_stream_chunk(
                                    chunk,
                                    stream_content_parts,
                                    stream_tool_calls_map,
                                    stream_reasoning_parts,
                                    in_text_block,
                                    in_reasoning_block,
                                ):
                                    yield event
                                    if event.type == "text_start":
                                        in_text_block = True
                                    elif event.type == "text_end":
                                        in_text_block = False
                                    elif event.type == "reasoning_start":
                                        in_reasoning_block = True
                                    elif event.type == "reasoning_end":
                                        in_reasoning_block = False

                            cost_tracker.add_usage(
                                input_tokens=stream_usage.input_tokens,
                                output_tokens=stream_usage.output_tokens,
                                reasoning_tokens=stream_usage.reasoning_tokens,
                            )
                            llm_span.set_meta("tool_calls_count", len(stream_tool_calls_map))
                            llm_span.set_meta("input_tokens", stream_usage.input_tokens)
                            llm_span.set_meta("output_tokens", stream_usage.output_tokens)
                    else:
                        async for chunk in self.provider.chat_stream(
                            prepared,
                            tools=tools,
                            model=None,
                            provider_options=self.run_config.provider_options,
                        ):
                            # Cooperative cancellation during token streaming
                            if self.cancel_event and self.cancel_event.is_set():
                                yield StreamChunk.done(content="", tool_calls=all_tool_calls)
                                return
                            if chunk.type == "usage" and chunk.usage:
                                stream_usage.input_tokens += chunk.usage.input_tokens
                                stream_usage.output_tokens += chunk.usage.output_tokens
                                stream_usage.reasoning_tokens += chunk.usage.reasoning_tokens
                                stream_usage.cache_read_tokens += chunk.usage.cache_read_tokens
                                stream_usage.cache_creation_tokens += chunk.usage.cache_creation_tokens
                                continue
                            async for event in self._process_stream_chunk(
                                chunk,
                                stream_content_parts,
                                stream_tool_calls_map,
                                stream_reasoning_parts,
                                in_text_block,
                                in_reasoning_block,
                            ):
                                yield event
                                if event.type == "text_start":
                                    in_text_block = True
                                elif event.type == "text_end":
                                    in_text_block = False
                                elif event.type == "reasoning_start":
                                    in_reasoning_block = True
                                elif event.type == "reasoning_end":
                                    in_reasoning_block = False

                        cost_tracker.add_usage(
                            input_tokens=stream_usage.input_tokens,
                            output_tokens=stream_usage.output_tokens,
                            reasoning_tokens=stream_usage.reasoning_tokens,
                        )

                except ProviderContextOverflowError:
                    overflow_retries += 1
                    logger.warning(f"stream_context_overflow iteration={iteration} retry={overflow_retries}")
                    yield StreamChunk.text_delta(
                        content=f"\n[Context overflow — compacting... retry {overflow_retries}/3]\n"
                    )

                    from src.sdk.middleware_summarization import SummarizationMiddleware

                    summary_mw = self.find_middleware(SummarizationMiddleware)
                    if summary_mw is not None:
                        success = await summary_mw.force_summarize(state)
                        if success:
                            if overflow_retries < 3:
                                continue

                    yield StreamChunk.error(message="Context too large after summarization attempt.")
                    break
                except Exception as e:
                    logger.error(f"llm_stream_error iteration={iteration}: {e}")
                    yield StreamChunk.error(message=str(e))
                    break

                if in_text_block:
                    yield StreamChunk.text_end()
                if in_reasoning_block:
                    yield StreamChunk.reasoning_end()

                for tc_data in stream_tool_calls_map.values():
                    args = tc_data.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args) if args else {}
                        except json.JSONDecodeError:
                            args = repair_tool_call(args)
                    stream_tool_calls.append(
                        ToolCall(
                            id=tc_data["id"],
                            name=tc_data["name"],
                            arguments=args,
                        )
                    )

                assistant_content = "".join(stream_content_parts)
                reasoning_content = "".join(stream_reasoning_parts) or None
                assistant_msg = Message.assistant(
                    content=assistant_content,
                    tool_calls=stream_tool_calls,
                    reasoning=reasoning_content,
                )

                # When the model uses interleaved reasoning (deepseek, kimi, etc.),
                # ensure the streaming pipeline correctly captures it. If reasoning
                # wasn't accumulated from stream events, fall back to the provider's
                # last known reasoning.
                if stream_tool_calls and not reasoning_content:
                    provider_reasoning = getattr(self.provider, "_last_reasoning", None)
                    if provider_reasoning:
                        assistant_msg.reasoning = provider_reasoning

                state.add_message(assistant_msg)

                await self._run_hooks("aafter_model", state)

                if not stream_tool_calls:
                    output_text = assistant_content
                    try:
                        await self._check_output_guardrails(output_text, state)
                    except GuardrailTripwire as e:
                        yield StreamChunk.error(message=f"Output blocked: {e.result.message}")
                    break

                # Deduplicate identical tool calls within a single LLM response.
                # Reasoning models (MiniMax M2.5, etc.) sometimes emit duplicate
                # tool_use blocks. Keep only the first occurrence per unique (name, args) pair.
                seen_keys: set[tuple[str, str]] = set()
                deduped_calls: list[ToolCall] = []
                for tc in stream_tool_calls:
                    key = (tc.name, json.dumps(tc.arguments, sort_keys=True))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        deduped_calls.append(tc)
                if len(deduped_calls) < len(stream_tool_calls):
                    logger.debug(
                        "sdk.deduped_tool_calls removed %d duplicates",
                        len(stream_tool_calls) - len(deduped_calls),
                    )
                stream_tool_calls = [self._with_runtime_context(tc) for tc in deduped_calls]

                # Record tool calls AFTER dedup so only unique names are reported
                all_tool_calls.extend([{"name": tc.name, "call_id": tc.id} for tc in stream_tool_calls])

                # Classify tool calls: parallel-safe, sequential, interrupts
                parallel_safe, sequential, interrupts = self._classify_tool_calls(stream_tool_calls)

                # Handle interrupts: yield interrupt events, add tool_result messages
                for tc in interrupts:
                    yield StreamChunk.interrupt(tool=tc.name, call_id=tc.id, args=tc.arguments)
                    interrupt_result = json.dumps(
                        {
                            "interrupt": True,
                            "tool": tc.name,
                            "args": tc.arguments,
                            "message": f"Tool '{tc.name}' requires user approval. When approved, retry the exact same {tc.name} call.",
                            "retry_on_approve": True,
                            "allowed_actions": ["approve", "reject", "edit"],
                        }
                    )
                    state.add_message(
                        Message.tool_result(
                            tool_call_id=tc.id,
                            content=interrupt_result,
                            name=tc.name,
                        )
                    )
                    yield StreamChunk.tool_result_event(
                        tool=tc.name, call_id=tc.id, result_preview=interrupt_result[:2000]
                    )
                    yield StreamChunk.tool_end(
                        tool=tc.name, call_id=tc.id, result_preview=interrupt_result[:2000]
                    )

                # Execute parallel-safe tools concurrently, emit events as they complete
                if parallel_safe:
                    if self.cancel_event and self.cancel_event.is_set():
                        yield StreamChunk.done(content="", tool_calls=all_tool_calls)
                        return
                    async for event in self._execute_tool_batch_streaming(parallel_safe, state):
                        yield event

                # Execute sequential (destructive) tools one-at-a-time
                for tc in sequential:
                    if self.cancel_event and self.cancel_event.is_set():
                        yield StreamChunk.done(content="", tool_calls=all_tool_calls)
                        return
                    async for event in self._execute_single_tool_streaming(tc, state):
                        yield event

        except SubagentCancelledError:
            await self._run_hooks("aafter_agent", state)
            raise

        await self._run_hooks("aafter_agent", state)

        # Drain pending stream events from middleware (e.g. rubric_evaluation_end)
        for event in state.extra.pop("_pending_stream_events", []):
            yield event

        # Re-run loop if middleware set _needs_rerun
        while state.extra.get("_needs_rerun"):
            state.extra["_needs_rerun"] = False
            await self._run_hooks("abefore_agent", state)
            # Re-enter the streaming ReAct loop (same inline loop as above)
            try:
                for iteration in range(self.run_config.max_iterations):
                    if self.cancel_event and self.cancel_event.is_set():
                        yield StreamChunk.done(content="", tool_calls=all_tool_calls)
                        return
                    limit_reason = cost_tracker.exceeds_limits(self.run_config)
                    if limit_reason:
                        yield StreamChunk.error(message=f"Run limit reached: {limit_reason}")
                        break
                    await self._check_subagent_before_llm(state)
                    await self._run_hooks("abefore_model", state)
                    prepared = self._prepare_messages(state)
                    tools = self._registry.list_tools() or None
                    stream_content_parts = []
                    stream_tool_calls = []
                    stream_tool_calls_map = {}
                    stream_reasoning_parts = []
                    in_text_block = False
                    in_reasoning_block = False
                    stream_usage = Usage()
                    try:
                        if self.trace_provider:
                            async with self.trace_provider.start_span(
                                SpanType.LLM_CALL, f"llm_call_rerun_{iteration}"
                            ) as llm_span:
                                async for chunk in self.provider.chat_stream(
                                    prepared, tools=tools, model=None,
                                    provider_options=self.run_config.provider_options,
                                ):
                                    if self.cancel_event and self.cancel_event.is_set():
                                        yield StreamChunk.done(content="", tool_calls=all_tool_calls)
                                        return
                                    if chunk.type == "usage" and chunk.usage:
                                        stream_usage.input_tokens += chunk.usage.input_tokens
                                        stream_usage.output_tokens += chunk.usage.output_tokens
                                        stream_usage.reasoning_tokens += chunk.usage.reasoning_tokens
                                        stream_usage.cache_read_tokens += chunk.usage.cache_read_tokens
                                        stream_usage.cache_creation_tokens += chunk.usage.cache_creation_tokens
                                        continue
                                    async for event in self._process_stream_chunk(
                                        chunk, stream_content_parts, stream_tool_calls_map,
                                        stream_reasoning_parts, in_text_block, in_reasoning_block,
                                    ):
                                        yield event
                                        if event.type == "text_start":
                                            in_text_block = True
                                        elif event.type == "text_end":
                                            in_text_block = False
                                        elif event.type == "reasoning_start":
                                            in_reasoning_block = True
                                        elif event.type == "reasoning_end":
                                            in_reasoning_block = False
                                cost_tracker.add_usage(
                                    input_tokens=stream_usage.input_tokens,
                                    output_tokens=stream_usage.output_tokens,
                                    reasoning_tokens=stream_usage.reasoning_tokens,
                                )
                                llm_span.set_meta("tool_calls_count", len(stream_tool_calls_map))
                                llm_span.set_meta("input_tokens", stream_usage.input_tokens)
                                llm_span.set_meta("output_tokens", stream_usage.output_tokens)
                        else:
                            async for chunk in self.provider.chat_stream(
                                prepared, tools=tools, model=None,
                                provider_options=self.run_config.provider_options,
                            ):
                                if self.cancel_event and self.cancel_event.is_set():
                                    yield StreamChunk.done(content="", tool_calls=all_tool_calls)
                                    return
                                if chunk.type == "usage" and chunk.usage:
                                    stream_usage.input_tokens += chunk.usage.input_tokens
                                    stream_usage.output_tokens += chunk.usage.output_tokens
                                    stream_usage.reasoning_tokens += chunk.usage.reasoning_tokens
                                    stream_usage.cache_read_tokens += chunk.usage.cache_read_tokens
                                    stream_usage.cache_creation_tokens += chunk.usage.cache_creation_tokens
                                    continue
                                async for event in self._process_stream_chunk(
                                    chunk, stream_content_parts, stream_tool_calls_map,
                                    stream_reasoning_parts, in_text_block, in_reasoning_block,
                                ):
                                    yield event
                                    if event.type == "text_start":
                                        in_text_block = True
                                    elif event.type == "text_end":
                                        in_text_block = False
                                    elif event.type == "reasoning_start":
                                        in_reasoning_block = True
                                    elif event.type == "reasoning_end":
                                        in_reasoning_block = False
                            cost_tracker.add_usage(
                                input_tokens=stream_usage.input_tokens,
                                output_tokens=stream_usage.output_tokens,
                                reasoning_tokens=stream_usage.reasoning_tokens,
                            )
                    except ProviderContextOverflowError:
                        yield StreamChunk.error(message="Context too large during re-run.")
                        break
                    except Exception as e:
                        logger.error(f"llm_stream_error rerun iteration={iteration}: {e}")
                        yield StreamChunk.error(message=str(e))
                        break

                    if in_text_block:
                        yield StreamChunk.text_end()
                    if in_reasoning_block:
                        yield StreamChunk.reasoning_end()

                    for tc_data in stream_tool_calls_map.values():
                        args = tc_data.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args) if args else {}
                            except json.JSONDecodeError:
                                args = repair_tool_call(args)
                        stream_tool_calls.append(
                            ToolCall(id=tc_data["id"], name=tc_data["name"], arguments=args)
                        )

                    assistant_content = "".join(stream_content_parts)
                    reasoning_content = "".join(stream_reasoning_parts) or None
                    assistant_msg = Message.assistant(
                        content=assistant_content, tool_calls=stream_tool_calls,
                        reasoning=reasoning_content,
                    )
                    state.add_message(assistant_msg)
                    await self._run_hooks("aafter_model", state)

                    if not stream_tool_calls:
                        break

                    effective_tool_calls = [self._with_runtime_context(tc) for tc in stream_tool_calls]
                    all_tool_calls.extend([{"name": tc.name, "call_id": tc.id} for tc in effective_tool_calls])
                    parallel_safe, sequential, interrupts = self._classify_tool_calls(effective_tool_calls)

                    for tc in interrupts:
                        yield StreamChunk.interrupt(tool=tc.name, call_id=tc.id, args=tc.arguments)
                        interrupt_result = json.dumps({
                            "interrupt": True, "tool": tc.name, "args": tc.arguments,
                            "message": f"Tool '{tc.name}' requires user approval.",
                            "retry_on_approve": True, "allowed_actions": ["approve", "reject", "edit"],
                        })
                        state.add_message(Message.tool_result(
                            tool_call_id=tc.id, content=interrupt_result, name=tc.name
                        ))
                        yield StreamChunk.tool_result_event(
                            tool=tc.name, call_id=tc.id, result_preview=interrupt_result[:2000]
                        )
                        yield StreamChunk.tool_end(
                            tool=tc.name, call_id=tc.id, result_preview=interrupt_result[:2000]
                        )

                    if parallel_safe:
                        if self.cancel_event and self.cancel_event.is_set():
                            yield StreamChunk.done(content="", tool_calls=all_tool_calls)
                            return
                        async for event in self._execute_tool_batch_streaming(parallel_safe, state):
                            yield event

                    for tc in sequential:
                        if self.cancel_event and self.cancel_event.is_set():
                            yield StreamChunk.done(content="", tool_calls=all_tool_calls)
                            return
                        async for event in self._execute_single_tool_streaming(tc, state):
                            yield event
            except SubagentCancelledError:
                await self._run_hooks("aafter_agent", state)
                raise

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

    async def _process_stream_chunk(
        self,
        chunk: StreamChunk,
        content_parts: list[str],
        tool_calls_map: dict[int, dict[str, Any]],
        reasoning_parts: list[str],
        in_text_block: bool,
        in_reasoning_block: bool,
    ) -> AsyncIterator[StreamChunk]:
        """Process a provider-emitted chunk, emitting block-structured events + backward-compat aliases.

        Providers emit both canonical and alias types (e.g. text_delta + ai_token).
        We skip alias input chunks here since we emit our own aliases below.
        """
        canonical = chunk.canonical_type
        # Provider-specific handling for chunk types that need special processing
        if chunk.type != canonical:
            return

        if canonical == "text_delta":
            if not in_text_block:
                yield StreamChunk.text_start()
            yield StreamChunk.text_delta(content=chunk.content)
            yield StreamChunk.ai_token(content=chunk.content)
            content_parts.append(chunk.content)

        elif canonical == "tool_input_start":
            if in_text_block:
                yield StreamChunk.text_end()
            chunk_args = chunk.args
            if isinstance(chunk_args, dict):
                chunk_args = self._with_runtime_context(
                    ToolCall(id=chunk.call_id or "", name=chunk.tool or "", arguments=chunk_args)
                ).arguments
            if chunk.call_id:
                tool_calls_map[len(tool_calls_map)] = {
                    "id": chunk.call_id,
                    "name": chunk.tool or "",
                    "arguments": "",
                }
            # Note: tool_input_start/tool_start events are emitted by
            # _execute_single_tool_streaming / _execute_tool_batch_streaming
            # to avoid duplicates. This branch only tracks state.

        elif canonical == "tool_input_delta":
            if chunk.content and chunk.call_id:
                for entry in tool_calls_map.values():
                    if entry["id"] == chunk.call_id:
                        entry["arguments"] += chunk.content
                        break
            yield StreamChunk.tool_input_delta(call_id=chunk.call_id or "", content=chunk.content)

        elif canonical == "tool_input_end":
            if chunk.call_id and chunk.tool:
                for entry in tool_calls_map.values():
                    if entry["id"] == chunk.call_id:
                        entry["name"] = chunk.tool
                        break
            yield StreamChunk.tool_input_end(call_id=chunk.call_id or "", tool=chunk.tool or "")

        elif canonical == "reasoning_delta":
            if not in_reasoning_block:
                yield StreamChunk.reasoning_start()
            yield StreamChunk.reasoning_delta(content=chunk.content)
            yield StreamChunk.reasoning(content=chunk.content)
            reasoning_parts.append(chunk.content)

        elif canonical == "reasoning_start":
            yield StreamChunk.reasoning_start()

        elif canonical == "reasoning_end":
            yield StreamChunk.reasoning_end()

        elif chunk.type == "text_start":
            yield StreamChunk.text_start()

        elif chunk.type == "text_end":
            yield StreamChunk.text_end()

        elif chunk.type == "tool_end":
            pass

        elif chunk.type == "done":
            pass

        elif chunk.type == "error":
            yield StreamChunk.error(message=chunk.content)

    async def run_single(self, messages: list[Message]) -> Message:
        """Single LLM call — no tool loop. For summarization, extraction, etc."""
        token = _current_agent_loop.set(self)
        try:
            prepared = list(messages)
            if self.system_prompt:
                if not prepared or prepared[0].role != "system":
                    prepared.insert(0, Message.system(self.system_prompt))

            response = await self.provider.chat(
                prepared, tools=None, model=None, provider_options=self.run_config.provider_options
            )

            if not isinstance(response.content, str):
                content = str(response.content)
            else:
                content = response.content

            return Message.assistant(content=content)
        finally:
            _current_agent_loop.reset(token)
