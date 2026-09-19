"""A tool call that never produced a result must never be reported as success.

Issue #24 review follow-up: `ToolDefinition.ainvoke` decided whether to await
based on `_coroutine`, a flag captured only at construction time. A coroutine
function attached afterwards took the `asyncio.to_thread` path instead, which
*calls* the function but never awaits it — returning an un-awaited coroutine
object. `ToolResult.from_raw` then stringified it into a non-error result, so
the governance executor recorded `executed: true` for a tool that never ran.
"""

import functools
import gc
import inspect
import threading
import warnings

import pytest

from src.sdk.tools import ToolDefinition, ToolResult


def _tool(**kwargs):
    return ToolDefinition(name="probe", description="probe", **kwargs)


async def _async_probe(value: str = "ok") -> str:
    return f"async:{value}"


def _sync_probe(value: str = "ok") -> str:
    return f"sync:{value}:thread={threading.get_ident() != MAIN_THREAD}"


MAIN_THREAD = threading.get_ident()


class TestAsyncDispatch:
    async def test_coroutine_attached_at_construction_is_awaited(self):
        result = await _tool(function=_async_probe).ainvoke({"value": "a"})
        assert result == "async:a"

    async def test_coroutine_attached_after_construction_is_awaited(self):
        """The regression: post-construction attachment returned a coroutine."""
        td = _tool()
        td.function = _async_probe
        result = await td.ainvoke({"value": "b"})
        assert not inspect.iscoroutine(result), "ainvoke returned an un-awaited coroutine"
        assert result == "async:b"

    async def test_reattached_coroutine_still_awaited(self):
        td = _tool(function=_sync_probe)
        td.function = _async_probe
        result = await td.ainvoke({"value": "c"})
        assert not inspect.iscoroutine(result)
        assert result == "async:c"

    async def test_post_construction_coroutine_result_is_not_a_success(self):
        """End-to-end: the wrapped ToolResult must carry the real value."""
        td = _tool()
        td.function = _async_probe
        raw = await td.ainvoke({"value": "d"})
        wrapped = ToolResult.from_raw(raw)
        assert wrapped.content == "async:d"
        assert "coroutine" not in wrapped.content


class TestSyncDispatch:
    async def test_sync_function_still_runs_off_the_event_loop(self):
        result = await _tool(function=_sync_probe).ainvoke({"value": "e"})
        assert result == "sync:e:thread=True"

    async def test_sync_function_attached_after_construction_still_runs(self):
        td = _tool()
        td.function = _sync_probe
        result = await td.ainvoke({"value": "f"})
        assert result == "sync:f:thread=True"


class TestFailurePropagation:
    @pytest.mark.parametrize("attach_late", [False, True])
    async def test_coroutine_failure_propagates(self, attach_late):
        """A raising tool must reach governance as a failure, not a value."""

        async def boom(**_kwargs):
            raise RuntimeError("provider exploded")

        td = _tool() if attach_late else _tool(function=boom)
        if attach_late:
            td.function = boom
        with pytest.raises(RuntimeError, match="provider exploded"):
            await td.ainvoke({})

    async def test_missing_function_still_raises(self):
        with pytest.raises(ValueError, match="no function bound"):
            await _tool().ainvoke({})

    async def test_sync_failure_propagates(self):
        def boom(**_kwargs):
            raise RuntimeError("sync exploded")

        with pytest.raises(RuntimeError, match="sync exploded"):
            await _tool(function=boom).ainvoke({})


def _wrapped_async_probe(value: str = "ok"):
    """A functools.wraps-style wrapper: iscoroutinefunction cannot see through it."""

    @functools.wraps(_async_probe)
    def wrapper(*args, **kwargs):
        return _async_probe(*args, **kwargs)

    return wrapper


class _AsyncCallable:
    """A callable object whose __call__ is async — also invisible to inspect."""

    async def __call__(self, value: str = "ok") -> str:
        return f"callable:{value}"


class TestSyncInvokeSeam:
    @pytest.mark.parametrize(
        "target",
        [
            pytest.param(_async_probe, id="async-def"),
            pytest.param(_wrapped_async_probe(), id="wrapped-async-def"),
            pytest.param(_AsyncCallable(), id="async-callable-object"),
        ],
    )
    def test_invoke_refuses_async_targets_instead_of_leaking_a_coroutine(self, target):
        """The sync seam must fail loudly for every async shape it can receive."""
        with pytest.raises(TypeError, match="use ainvoke"):
            _tool(function=target).invoke({"value": "g"})

    def test_invoke_leaves_no_orphan_coroutine(self):
        """Refusing must not leave an un-awaited coroutine behind."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(TypeError, match="use ainvoke"):
                _tool(function=_wrapped_async_probe()).invoke({"value": "g"})
            gc.collect()
        assert not [w for w in caught if "never awaited" in str(w.message)]

    def test_invoke_still_runs_sync_tools(self):
        assert _tool(function=_sync_probe).invoke({"value": "h"}).startswith("sync:h")
