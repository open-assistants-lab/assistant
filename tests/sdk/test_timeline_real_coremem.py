"""#127: message_timeline must work against the REAL pinned CoreMem."""

import pytest

from src.sdk.tools_core import message as message_tools


@pytest.mark.asyncio
async def test_timeline_uses_current_coremem_api(monkeypatch):
    """No fake: assert the attribute the tool calls exists on the pinned class."""
    from coremem import MemoryCore

    assert not hasattr(MemoryCore, "search_enhanced"), (
        "the removed API came back; this pin must be revisited"
    )
    assert hasattr(MemoryCore, "recall")

    import inspect

    from src.sdk.tools_core import message as mod

    fn = getattr(mod.message_timeline, "function", mod.message_timeline)
    source = inspect.getsource(fn)
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    assert "search_enhanced" not in code, "message_timeline still calls the removed API"
    assert "recall(" in source, "message_timeline must use the pinned recall() API"

    class _Core:
        def __init__(self):
            self.calls = []
        def recall(self, query, **kwargs):
            self.calls.append((query, kwargs))
            return []
    core = _Core()
    monkeypatch.setattr(mod, "_get_message_core", lambda *a, **k: core)
    out = message_tools.message_timeline.invoke({"query": "q", "user_id": "u"})
    assert "No matching events" in str(out)
    assert core.calls, "the tool never called recall()"
    assert core.calls[0][0] == "q"
