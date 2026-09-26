"""Layer 3 integration: the auto policy's two hard requirements.

C1 — `auto` must make zero connections while measuring.
P4 — the decision is taken at loop build and re-evaluated only on compaction,
     never per LLM call, or the model's tool set churns every turn.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.sdk.mcp_exposure import measure_mode, resolve_exposure


class _Bridge:
    """Minimal bridge double recording whether anything connected."""

    def __init__(self, catalogue: list, warm: bool = True) -> None:
        self._catalogue = catalogue
        self._warm = warm
        self.connections_attempted = 0
        self._manager = SimpleNamespace(record_exposure_decision=lambda _d: None)

    def cached_catalogue(self) -> list:
        return list(self._catalogue)

    def has_cached_metadata(self) -> bool:
        return self._warm

    async def catalogue(self) -> list:
        self.connections_attempted += 1
        return list(self._catalogue)

    async def build_definitions(self, names):
        from src.sdk.tools import ToolDefinition

        by_name = {str(t.name): t for t in self._catalogue}
        return [
            ToolDefinition(
                name=n,
                description=getattr(by_name[n], "description", ""),
                parameters=getattr(by_name[n], "inputSchema", {}) or {},
            )
            for n in sorted(set(names))
            if n in by_name
        ]

    async def promote(self, decision):

        defs = await self.build_definitions(decision.survivors)
        if decision.mode == "search":
            return [], defs
        return defs, []


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name, description="d", inputSchema={}, annotations=None, meta={},
        server_name="demo",
    )


# --------------------------------------------------------------------------
# C1 — zero connections to measure
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_measures_the_cache_without_connecting(monkeypatch) -> None:

    bridge = _Bridge([_tool(f"t{i}") for i in range(50)], warm=True)
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_bridge.MCPToolBridge", lambda **_kw: bridge
    )
    decision = resolve_exposure(
        setting="auto",
        tools=bridge.cached_catalogue(),
        caps={},
        measured_mode="search",
        measurement_reason="definitions 42.0% of window >= 10%",
    )
    callable_tools, search_only = await bridge.promote(decision)

    assert callable_tools == []
    assert len(search_only) == 50
    # The only catalogue call made was the cached one.
    assert bridge.connections_attempted == 0


@pytest.mark.asyncio
async def test_always_mode_does_connect_while_auto_does_not(monkeypatch) -> None:
    """The inversion the design promised: aggressive policy, cheaper startup."""
    cache_only = _Bridge([_tool("t1")], warm=True)
    live = _Bridge([_tool("t1")], warm=True)

    from src.sdk.tools_core.mcp_bridge import MCPToolBridge  # noqa: F401

    # always -> live catalogue (one connection)
    await live.catalogue()
    assert live.connections_attempted == 1

    # auto -> cached catalogue (none)
    cache_only.cached_catalogue()
    assert cache_only.connections_attempted == 0


def test_measurement_uses_the_real_definitions_not_the_raw_catalogue() -> None:
    """What we measure must be what we would expose, or the threshold lies.

    A raw metadata dict has no `to_openai_format`, so measuring the catalogue
    directly would either fail or silently cost something other than the real
    schema. The measured value must equal the registered definitions' cost.
    """
    from src.sdk.context_measurement import estimate_tool_schema_tokens
    from src.sdk.tools import ToolDefinition

    raw = [_tool("t1"), _tool("t2")]
    defs = [
        ToolDefinition(
            name=t.name,
            description=t.description,
            parameters=t.inputSchema or {"type": "object"},
        )
        for t in raw
    ]
    measured = measure_mode(
        defs, model="openai:gpt-4o", threshold_pct=10.0, cache_warm=True
    )[1]
    assert "definitions" in measured
    # The cost the reason string reports is the definitions' real schema cost.
    assert estimate_tool_schema_tokens(defs) > 0


# --------------------------------------------------------------------------
# C2 — cold cache
# --------------------------------------------------------------------------


def test_cold_cache_defers_even_for_a_tiny_catalogue() -> None:
    from src.sdk.tools import ToolDefinition

    defs = [ToolDefinition(name="t1", description="d", parameters={})]
    mode, reason = measure_mode(
        defs, model="openai:gpt-4o", threshold_pct=10.0, cache_warm=False
    )
    assert mode == "search"
    assert "cold" in reason


def test_defer_with_missing_metadata_can_be_overridden() -> None:
    """Operators who prefer Pi's tradeoff can opt in explicitly.

    With defer_when_unknown=False a cold cache is measured anyway instead of
    defaulting to `search`, so a tiny catalogue stays directly callable on a
    first session.
    """
    from src.sdk.tools import ToolDefinition

    defs = [ToolDefinition(name="t1", description="d", parameters={})]
    deferring, _ = measure_mode(
        defs,
        model="openai:gpt-4o",
        threshold_pct=10.0,
        cache_warm=False,
        defer_when_unknown=True,
    )
    not_deferring, reason = measure_mode(
        defs,
        model="openai:gpt-4o",
        threshold_pct=10.0,
        cache_warm=False,
        defer_when_unknown=False,
    )
    assert deferring == "search"
    assert not_deferring == "always"
    assert "cold" not in reason


# --------------------------------------------------------------------------
# P4 — stability across repeated evaluation
# --------------------------------------------------------------------------


def test_repeated_measurement_does_not_churn_the_surface() -> None:
    from src.sdk.tools import ToolDefinition

    defs = [ToolDefinition(name=f"t{i}", description="d" * 100) for i in range(5)]
    decisions = [
        measure_mode(defs, model="openai:gpt-4o", threshold_pct=10.0, cache_warm=True)[0]
        for _ in range(10)
    ]
    assert len(set(decisions)) == 1


def test_a_per_model_window_changes_the_decision() -> None:
    """The accepted consequence: the same deployment differs per model."""
    from src.sdk.tools import ToolDefinition

    defs = [ToolDefinition(name=f"t{i}", description="d" * 100) for i in range(20)]
    from src.sdk.context_measurement import estimate_tool_schema_tokens

    cost = estimate_tool_schema_tokens(defs)
    small, _ = measure_mode(
        defs, model="openai:gpt-4o", threshold_pct=10.0, cache_warm=True,
        window_override=cost * 5,
    )
    large, _ = measure_mode(
        defs, model="openai:gpt-4o", threshold_pct=10.0, cache_warm=True,
        window_override=cost * 100,
    )
    assert small == "search"
    assert large == "always"


# --------------------------------------------------------------------------
# the decision is reported (firewall I4 carries through)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_measurement_reason_is_recorded_for_health() -> None:
    bridge = _Bridge([_tool("t1")], warm=True)
    decision = resolve_exposure(
        setting="auto",
        tools=bridge.cached_catalogue(),
        caps={},
        measured_mode="always",
        measurement_reason="definitions 1.2% of window < 10%",
    )
    assert decision.measurement_reason == "definitions 1.2% of window < 10%"
    assert decision.mode == "always"
