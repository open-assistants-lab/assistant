"""Tests for SDK tool system — @tool decorator, ToolDefinition, ToolRegistry."""


import pytest

from src.sdk.tools import ToolDefinition, ToolRegistry, tool

# ─── @tool decorator ───


class TestToolDecorator:
    def test_basic_decoration(self):
        @tool
        def greet(name: str) -> str:
            """Say hello."""
            return f"Hello {name}"

        assert isinstance(greet, ToolDefinition)
        assert greet.name == "greet"
        assert greet.description == "Say hello."

    def test_custom_name(self):
        @tool(name="custom_name")
        def my_func(x: int) -> str:
            """Does something."""
            return str(x)

        assert my_func.name == "custom_name"

    def test_invoke(self):
        @tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        result = add.invoke({"a": 1, "b": 2})
        assert result == 3

    def test_invoke_with_kwargs(self):
        @tool
        def greet(name: str = "World") -> str:
            """Greet someone."""
            return f"Hello {name}"

        assert greet.invoke({"name": "Alice"}) == "Hello Alice"
        assert greet.invoke({}) == "Hello World"

    def test_description_extraction(self):
        @tool
        def my_tool(x: int) -> str:
            """First line is description.

            More details here.
            """
            return str(x)

        assert my_tool.description == "First line is description."

    def test_empty_description(self):
        @tool
        def no_doc(x: int) -> str:
            return str(x)

        assert no_doc.description == ""


class TestToolSchema:
    def test_required_params(self):
        @tool
        def search(query: str, limit: int = 10) -> str:
            """Search."""
            return query

        assert "query" in search.parameters["properties"]
        assert "required" in search.parameters
        assert "query" in search.parameters["required"]
        assert "limit" not in search.parameters["required"]

    def test_default_values(self):
        @tool
        def get_item(id: str, format: str = "json", verbose: bool = False) -> str:
            """Get item."""
            return id

        props = get_item.parameters["properties"]
        assert props["format"]["default"] == "json"
        assert props["verbose"]["default"] is False

    def test_type_hints(self):
        @tool
        def typed(x: int, y: str, z: bool, items: list) -> str:
            """Typed."""
            return ""

        props = typed.parameters["properties"]
        assert props["x"]["type"] == "integer"
        assert props["y"]["type"] == "string"
        assert props["z"]["type"] == "boolean"
        assert props["items"]["type"] == "array"

    def test_optional_type(self):
        @tool
        def with_optional(query: str, timezone: str | None = None) -> str:
            """With optional."""
            return query

        props = with_optional.parameters["properties"]
        assert "anyOf" in props["timezone"]

    def test_no_params(self):
        @tool
        def no_params() -> str:
            """No params."""
            return "ok"

        assert no_params.parameters["properties"] == {}

    def test_user_id_default(self):
        @tool
        def my_tool(user_id: str = "default_user") -> str:
            """My tool."""
            return user_id

        props = my_tool.parameters["properties"]
        assert props["user_id"]["default"] == "default_user"
        assert "user_id" not in my_tool.parameters.get("required", [])


class TestToolOpenAIFormat:
    def test_to_openai_format(self):
        @tool
        def time_get(user_id: str = "default_user") -> str:
            """Get current time."""
            return "3pm"

        result = time_get.to_openai_format()
        assert result["type"] == "function"
        assert result["function"]["name"] == "time_get"
        assert result["function"]["description"] == "Get current time."
        assert "parameters" in result["function"]

    def test_to_anthropic_format(self):
        @tool
        def time_get(user_id: str = "default_user") -> str:
            """Get current time."""
            return "3pm"

        result = time_get.to_anthropic_format()
        assert result["name"] == "time_get"
        assert result["description"] == "Get current time."
        assert "input_schema" in result


class TestToolAsync:
    @pytest.mark.asyncio
    async def test_async_invoke(self):
        @tool
        async def async_tool(x: int) -> str:
            """Async tool."""
            return str(x)

        result = await async_tool.ainvoke({"x": 42})
        assert result == "42"

    @pytest.mark.asyncio
    async def test_sync_via_ainvoke(self):
        @tool
        def sync_tool(x: int) -> str:
            """Sync tool."""
            return str(x)

        result = await sync_tool.ainvoke({"x": 42})
        assert result == "42"


class TestToolNoFunction:
    def test_invoke_without_function(self):
        td = ToolDefinition(name="empty", description="No func", parameters={})
        with pytest.raises(ValueError, match="no function"):
            td.invoke({})


# ─── ToolRegistry ───


class TestToolRegistry:
    def test_register_function(self):
        registry = ToolRegistry()

        @tool
        def my_tool(x: int) -> str:
            """Test."""
            return str(x)

        result = registry.register(my_tool)
        assert result.name == "my_tool"
        assert len(registry) == 1

    def test_register_callable(self):
        registry = ToolRegistry()

        def my_tool(x: int) -> str:
            """Test."""
            return str(x)

        result = registry.register(my_tool)
        assert result.name == "my_tool"

    def test_register_with_custom_name(self):
        registry = ToolRegistry()

        def my_tool(x: int) -> str:
            """Test."""
            return str(x)

        result = registry.register(my_tool, name="custom_name")
        assert result.name == "custom_name"

    def test_duplicate_rejected(self):
        registry = ToolRegistry()

        @tool
        def dup(x: int) -> str:
            """First."""
            return str(x)

        registry.register(dup)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(dup)

    def test_get(self):
        registry = ToolRegistry()

        @tool
        def my_tool(x: int) -> str:
            """Test."""
            return str(x)

        registry.register(my_tool)
        found = registry.get("my_tool")
        assert found is not None
        assert found.name == "my_tool"

    def test_get_missing(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_has(self):
        registry = ToolRegistry()

        @tool
        def my_tool(x: int) -> str:
            """Test."""
            return str(x)

        registry.register(my_tool)
        assert registry.has("my_tool")
        assert not registry.has("nonexistent")
        assert "my_tool" in registry
        assert "nope" not in registry

    def test_remove(self):
        registry = ToolRegistry()

        @tool
        def my_tool(x: int) -> str:
            """Test."""
            return str(x)

        registry.register(my_tool)
        assert registry.remove("my_tool")
        assert len(registry) == 0
        assert not registry.remove("nonexistent")

    def test_list(self):
        registry = ToolRegistry()

        @tool
        def tool_a(x: int) -> str:
            """A."""
            return "a"

        @tool
        def tool_b(y: int) -> str:
            """B."""
            return "b"

        registry.register(tool_a)
        registry.register(tool_b)
        names = registry.list_names()
        assert "tool_a" in names
        assert "tool_b" in names

    def test_to_openai_format(self):
        registry = ToolRegistry()

        @tool
        def time_get(user_id: str = "default_user") -> str:
            """Get time."""
            return "3pm"

        registry.register(time_get)
        result = registry.to_openai_format()
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "time_get"

    def test_to_anthropic_format(self):
        registry = ToolRegistry()

        @tool
        def time_get(user_id: str = "default_user") -> str:
            """Get time."""
            return "3pm"

        registry.register(time_get)
        result = registry.to_anthropic_format()
        assert len(result) == 1
        assert result[0]["name"] == "time_get"
        assert "input_schema" in result[0]


# ─── Sync-tool offloading (audit S1) ───


class TestSyncToolOffload:
    @pytest.mark.asyncio
    async def test_ainvoke_offloads_sync_tool(self):
        """Two concurrent slow sync tools must overlap — proving no event-loop blocking."""
        import asyncio
        import time as _time
        calls = {"n": 0}

        @tool
        def slow_tool(x: int) -> int:
            """Sleeps then returns x."""
            calls["n"] += 1
            _time.sleep(0.2)
            return x * 2

        loop = asyncio.get_running_loop()
        start = loop.time()
        results = await asyncio.gather(slow_tool.ainvoke({"x": 1}), slow_tool.ainvoke({"x": 2}))
        elapsed = loop.time() - start
        assert results == [2, 4]
        assert elapsed < 0.35  # serial would be >= 0.4

    @pytest.mark.asyncio
    async def test_ainvoke_still_supports_coroutine_tools(self):
        """Coroutine tools keep awaiting directly (no thread offload)."""
        import asyncio

        @tool
        async def async_tool(x: int) -> int:
            """Async tool."""
            await asyncio.sleep(0)
            return x + 1

        assert await async_tool.ainvoke({"x": 1}) == 2
