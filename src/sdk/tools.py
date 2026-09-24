"""Tool definition and registry for the agent SDK.

The @tool decorator extracts JSON Schema from type hints + docstring.
ToolRegistry provides OpenAI/Anthropic format output for LLM API calls.

Key compatibility:
    - @tool produces objects with .name, .description, .args, .invoke, .ainvoke
    - .invoke() and .ainvoke() accept a dict, returning the function result
    - .invoke() is the sync seam and rejects async tools (use .ainvoke()); it
      never returns an un-awaited coroutine
    - .to_openai_format() / .to_anthropic_format() for LLM tool definitions
"""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Callable
from typing import Any, Literal, get_type_hints

from pydantic import BaseModel, Field, field_validator, model_validator

from src.sdk.execution_models import Outcome


class ExternalHTTPExecutor(BaseModel):
    """Trusted deployment-owned HTTP endpoint for an async tool.

    ``dispatch_url`` comes from trusted tool metadata, never model arguments.
    Deployments must restrict it to an internal/allowlisted executor; this
    validation prevents malformed URLs but is not a general SSRF policy.
    """

    kind: Literal["external_http"]
    dispatch_url: str
    manifest_hash: str | None = None

    @field_validator("dispatch_url")
    @classmethod
    def validate_dispatch_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("dispatch_url must be an absolute http(s) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("dispatch_url must not contain userinfo")
        return value


DEFAULT_COMMAND_TIMEOUT_SECONDS = 300.0


class ToolAnnotations(BaseModel):
    """Metadata about a tool's behavior for auto-approval and UI display."""
    title: str | None = None
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False
    # M4 (issue #6): declared by the tool author; permission resolution maps
    # this to "ask" unless an item permission overrides it.
    requires_approval: bool = False
    # Issue #21: async execution is opt-in; existing tools retain synchronous
    # approval/execution behavior.
    execution_mode: Literal["sync", "async"] = "sync"
    executor: ExternalHTTPExecutor | None = None
    # Issue #23: per-tool command budget. Custom TOOL.md tools may declare
    # `timeout_seconds` in their annotations block; any positive value is
    # accepted with no ceiling, and the literal string "none" explicitly
    # opts out of the cap. Zero, negative, boolean, non-finite, and
    # non-numeric declarations are rejected rather than silently changing
    # the cap's meaning.
    # Declared strictly: the before-validator below normalises the string
    # forms ('none', '42') before the field is populated, so no str ever
    # reaches this value.
    timeout_seconds: float | None = DEFAULT_COMMAND_TIMEOUT_SECONDS

    # Assignment validation is required: TOOL.md annotations and lazy-load
    # reconstruction set this after construction, and an unvalidated
    # non-positive value would silently disable the cap.
    model_config = {"validate_assignment": True}

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _validate_timeout(cls, value: Any) -> float | None:
        """Reject values that would silently change the cap's meaning.

        Runs BEFORE pydantic's union coercion: otherwise a YAML `true` is
        lax-coerced to 1.0 and becomes a one-second cap instead of being
        rejected as malformed.
        """
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(
                f"timeout_seconds must be a positive number or 'none', got {value!r}"
            )
        if isinstance(value, str):
            if value.strip().lower() == "none":
                return None
            try:
                value = float(value)
            except ValueError:
                raise ValueError(
                    f"timeout_seconds must be a positive number or 'none', got {value!r}"
                ) from None
        if not isinstance(value, (int, float)):
            raise ValueError(f"timeout_seconds must be a positive number or 'none', got {value!r}")
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"timeout_seconds must be a positive number or 'none', got {value!r}")
        return float(value)


class ToolResult(BaseModel):
    """Structured result from a tool execution.

    Tools can return either a plain string (auto-wrapped as ToolResult) or
    a ToolResult instance for richer output:
      - content: human-readable text sent to LLM
      - structured_content: machine-parseable dict (optional)
      - is_error: marks the result as an error
      - audience: who sees this result (default: assistant only)
    """

    content: str
    structured_content: dict[str, Any] | None = None
    is_error: bool = False
    outcome: Outcome | None = None
    audience: list[str] = Field(default_factory=lambda: ["assistant"])

    @model_validator(mode="after")
    def _infer_default_outcome(self) -> ToolResult:
        if self.outcome is None:
            self.outcome = Outcome.FAILED if self.is_error else Outcome.SUCCEEDED
        return self

    @classmethod
    def from_raw(cls, result: Any) -> ToolResult:
        """Wrap a raw tool return value as ToolResult.

        If the tool already returned a ToolResult, pass it through.
        If it returned a string, wrap it. Otherwise, stringify.
        """
        if isinstance(result, ToolResult):
            return result
        if isinstance(result, str):
            return cls(content=result)
        return cls(content=str(result))


class ToolDefinition(BaseModel):
    """A tool definition with name, description, parameter schema, and callable."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    annotations: ToolAnnotations = Field(default_factory=ToolAnnotations)
    output_schema: dict[str, Any] | None = None
    function: Callable[..., Any] | None = Field(default=None, exclude=True)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def args(self) -> dict[str, Any]:
        return self.parameters

    def invoke(self, args: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if args is None:
            args = {}
        merged = {**args, **kwargs}
        if self.function is None:
            raise ValueError(f"Tool {self.name} has no function bound")
        if inspect.iscoroutinefunction(self.function):
            # Never hand a coroutine to a caller that cannot await it: an
            # un-run tool would otherwise be mistaken for a result.
            raise TypeError(
                f"Tool '{self.name}' is async; use ainvoke() instead of invoke()"
            )
        result = self.function(**merged)
        if inspect.isawaitable(result):
            # iscoroutinefunction cannot see through every wrapper (functools
            # .wraps over an async def, a callable object with an async
            # __call__), so guard the result too — and close the orphan rather
            # than leaving an un-awaited coroutine behind.
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError(
                f"Tool '{self.name}' returned an awaitable; use ainvoke() instead of invoke()"
            )
        return result

    async def ainvoke(self, args: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if args is None:
            args = {}
        merged = {**args, **kwargs}
        if self.function is None:
            raise ValueError(f"Tool {self.name} has no function bound")
        # Decide from the CURRENT callable, never a construction-time flag: a
        # coroutine attached after __init__ (lazy rebuild, plugin registration)
        # used to take the thread path, which called the function without
        # awaiting it and returned an un-awaited coroutine object that
        # from_raw then stringified into a non-error result (#24 review).
        if inspect.iscoroutinefunction(self.function):
            result = self.function(**merged)
        else:
            # Sync tool bodies run in a worker thread so they never block the
            # event loop (audit S1: SQLite/subprocess/IMAP tools stalled every
            # concurrent session). to_thread copies contextvars, so
            # get_current_agent_loop() consumers keep working.
            result = await asyncio.to_thread(self.function, **merged)
        # Belt-and-braces for callables `iscoroutinefunction` cannot see
        # through (functools.partial, some decorators): awaiting the result is
        # always safe, and returning an awaitable never is.
        if inspect.isawaitable(result):
            result = await result
        return result

    def to_openai_format(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
        if self.annotations.title or self.annotations.read_only or self.annotations.destructive:
            result["function"]["annotations"] = self.annotations.model_dump(exclude_none=True)
        if self.output_schema:
            result["function"]["output_schema"] = self.output_schema
        return result

    def to_anthropic_format(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }
        if self.output_schema:
            result["output_schema"] = self.output_schema
        return result


_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _python_type_to_json_schema(tp: Any) -> dict[str, Any]:
    origin = getattr(tp, "__origin__", None)
    if origin is list:
        args = getattr(tp, "__args__", None)
        schema: dict[str, Any] = {"type": "array"}
        if args:
            schema["items"] = _python_type_to_json_schema(args[0])
        return schema
    if origin is dict:
        return {"type": "object"}
    if tp in _TYPE_MAP:
        return {"type": _TYPE_MAP[tp]}
    if tp is Any:
        return {}
    return {"type": "string"}


def _extract_tool_schema(func: Callable[..., Any], name: str | None = None) -> ToolDefinition:
    """Extract ToolDefinition from a function's type hints and docstring."""
    tool_name = name or func.__name__
    doc = inspect.getdoc(func) or ""
    description = doc.split("\n\n")[0].strip() if doc else ""

    hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}
    sig = inspect.signature(func)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        prop: dict[str, Any] = {}
        if param_name in hints:
            hint = hints[param_name]
            if hasattr(hint, "__origin__") and hint.__origin__ is type(None):
                continue
            none_type = type(None)
            if hasattr(hint, "__args__") and none_type in getattr(hint, "__args__", ()):
                non_none = [a for a in hint.__args__ if a is not none_type]
                if non_none:
                    prop = _python_type_to_json_schema(non_none[0])
                    prop["anyOf"] = [_python_type_to_json_schema(non_none[0]), {"type": "null"}]
                    del prop["type"]
                else:
                    prop = {"type": "string"}
            else:
                prop = _python_type_to_json_schema(hint)
        else:
            prop = {"type": "string"}

        if param.default is inspect.Parameter.empty:
            required.append(param_name)
        else:
            prop["default"] = param.default

        param_title = param_name.replace("_", " ").title()
        prop["title"] = param_title
        properties[param_name] = prop

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters["required"] = required

    return ToolDefinition(
        name=tool_name,
        description=description,
        parameters=parameters,
        function=func,
    )


def tool(func: Callable[..., Any] | None = None, *, name: str | None = None) -> Any:
    """Decorator that converts a function into a ToolDefinition.

    Usage:
        @tool
        def time_get(user_id: str =  DEFAULT_USER_ID) -> str:
            '''Get the current time.'''
            ...

        @tool(name="custom_name")
        def my_func(x: int) -> str:
            '''Does something.'''
            ...
    """
    if func is not None:
        return _extract_tool_schema(func, name)

    def decorator(fn: Callable[..., Any]) -> ToolDefinition:
        return _extract_tool_schema(fn, name)

    return decorator


class ToolRegistry:
    """Registry for tools available to an agent.

    Provides deduplication, lookup, and format conversion.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self, func_or_tool: Callable[..., Any] | ToolDefinition, *, name: str | None = None
    ) -> ToolDefinition:
        if isinstance(func_or_tool, ToolDefinition):
            td = func_or_tool
            if name:
                td = ToolDefinition(
                    name=name,
                    description=td.description,
                    parameters=td.parameters,
                    annotations=td.annotations,
                    output_schema=td.output_schema,
                    function=td.function,
                )
        elif callable(func_or_tool):
            td = _extract_tool_schema(func_or_tool, name)
        else:
            raise TypeError(f"Expected callable or ToolDefinition, got {type(func_or_tool)}")

        if td.name in self._tools:
            raise ValueError(f"Tool '{td.name}' already registered")
        self._tools[td.name] = td
        return td

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def has(self, name: str) -> bool:
        return name in self._tools

    def remove(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def to_openai_format(self) -> list[dict[str, Any]]:
        return [td.to_openai_format() for td in self._tools.values()]

    def to_anthropic_format(self) -> list[dict[str, Any]]:
        return [td.to_anthropic_format() for td in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
