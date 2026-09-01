"""Tool definition and registry for the agent SDK.

The @tool decorator extracts JSON Schema from type hints + docstring.
ToolRegistry provides OpenAI/Anthropic format output for LLM API calls.

Key compatibility:
    - @tool produces objects with .name, .description, .args, .invoke, .ainvoke
    - .invoke() and .ainvoke() accept a dict, returning the function result
    - .to_openai_format() / .to_anthropic_format() for LLM tool definitions
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic import BaseModel, Field


class ToolAnnotations(BaseModel):
    """Metadata about a tool's behavior for auto-approval and UI display."""

    title: str | None = None
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False
    # M4 (issue #6): declared by the tool author; the governance tier
    # resolution maps this to "explicit" unless a settings tier overrides it.
    requires_approval: bool = False


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
    audience: list[str] = Field(default_factory=lambda: ["assistant"])

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
    _coroutine: Any | None = None

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        func = data.get("function")
        if func and inspect.iscoroutinefunction(func):
            self._coroutine = func

    @property
    def args(self) -> dict[str, Any]:
        return self.parameters

    def invoke(self, args: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if args is None:
            args = {}
        merged = {**args, **kwargs}
        if self.function is None:
            raise ValueError(f"Tool {self.name} has no function bound")
        return self.function(**merged)

    async def ainvoke(self, args: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if args is None:
            args = {}
        merged = {**args, **kwargs}
        if self.function is None:
            raise ValueError(f"Tool {self.name} has no function bound")
        if self._coroutine:
            return await self._coroutine(**merged)
        # Sync tool bodies run in a worker thread so they never block the
        # event loop (audit S1: SQLite/subprocess/IMAP tools stalled every
        # concurrent session). to_thread copies contextvars, so
        # get_current_agent_loop() consumers keep working.
        return await asyncio.to_thread(self.function, **merged)

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
