"""MCP Layer 3 — the measured `auto` policy (Axis A).

`auto` is the one idea none of the other harnesses implement: measure the cost
of the deferrable tool definitions against the model's context window and
decide, instead of asking the operator to guess. Borrowed from Claude Code as a
*policy*; its mechanism is unavailable to us, so this measures through our own
index and metadata cache.
"""

from __future__ import annotations

from src.sdk.mcp_exposure import measure_mode, parse_auto

# --------------------------------------------------------------------------
# setting parsing
# --------------------------------------------------------------------------


def test_bare_auto_means_ten_percent() -> None:
    assert parse_auto("auto") == 10.0


def test_auto_with_explicit_threshold() -> None:
    assert parse_auto("auto:5") == 5.0
    assert parse_auto("auto:25") == 25.0


def test_non_auto_settings_have_no_threshold() -> None:
    for setting in ("always", "search", "never", "direct", "hybrid", "proxy", ""):
        assert parse_auto(setting) is None


def test_malformed_auto_is_rejected_rather_than_defaulted() -> None:
    for bad in ("auto:", "auto:abc", "auto:0", "auto:-5"):
        assert parse_auto(bad) is None, f"{bad} should not yield a threshold"


# --------------------------------------------------------------------------
# the measurement itself
# --------------------------------------------------------------------------


def _definition(name: str, description: str = "d" * 200) -> object:
    from src.sdk.tools import ToolDefinition

    return ToolDefinition(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {"a": {"type": "string"}}},
    )


def test_small_catalogue_stays_always() -> None:
    tools = [_definition("t1")]
    mode, reason = measure_mode(
        tools, model="openai:gpt-4o", threshold_pct=10.0, cache_warm=True
    )
    assert mode == "always"
    assert "window" in reason


def test_large_catalogue_defers_to_search() -> None:
    # 400 fat definitions is far more than 10% of any realistic window.
    tools = [_definition(f"tool_{i}") for i in range(400)]
    mode, reason = measure_mode(
        tools, model="openai:gpt-4o", threshold_pct=10.0, cache_warm=True
    )
    assert mode == "search"
    assert ">=" in reason


def test_threshold_boundary_is_inclusive() -> None:
    """At exactly the threshold we defer; below it we do not (C-style 10% rule)."""
    tools = [_definition("t1")]
    from src.sdk.context_measurement import estimate_tool_schema_tokens

    cost = estimate_tool_schema_tokens(tools)  # type: ignore[arg-type]
    # A synthetic window that puts the cost at exactly 10%.
    window = cost * 10
    at_threshold, _ = measure_mode(
        tools,
        model="openai:gpt-4o",
        threshold_pct=10.0,
        cache_warm=True,
        window_override=window,
    )
    below, _ = measure_mode(
        tools,
        model="openai:gpt-4o",
        threshold_pct=10.0,
        cache_warm=True,
        window_override=window + 1,
    )
    assert at_threshold == "search"
    assert below == "always"


def test_cold_cache_defers_rather_than_injecting_an_uncosted_catalogue() -> None:
    """C2: a catalog we have never costed against must not be injected whole."""
    mode, reason = measure_mode(
        [_definition("t1")],
        model="openai:gpt-4o",
        threshold_pct=10.0,
        cache_warm=False,
    )
    assert mode == "search"
    assert "cache" in reason.lower()


def test_unknown_context_window_defers() -> None:
    mode, reason = measure_mode(
        [_definition("t1")],
        model="totally-unknown-provider:no-such-model",
        threshold_pct=10.0,
        cache_warm=True,
    )
    assert mode == "search"
    assert "window" in reason.lower()


# --------------------------------------------------------------------------
# resolve_exposure accepts a measured mode for auto
# --------------------------------------------------------------------------


def test_auto_without_a_measured_mode_is_a_configuration_error() -> None:
    """Unmeasured auto must never silently assume a permissive mode."""
    from src.sdk.mcp_exposure import resolve_exposure

    decision = resolve_exposure(setting="auto", tools=[], caps={})
    assert decision.error is not None
    assert "measured" in decision.error


def test_auto_uses_the_supplied_measured_mode() -> None:
    from src.sdk.mcp_exposure import resolve_exposure

    decision = resolve_exposure(
        setting="auto",
        tools=[],
        caps={},
        measured_mode="search",
    )
    assert decision.mode == "search"
    assert decision.error is None
    assert decision.setting == "auto"


def test_measured_mode_is_ignored_for_explicit_settings() -> None:
    """An explicit setting wins; auto's measurement never overrides it."""
    from src.sdk.mcp_exposure import resolve_exposure

    decision = resolve_exposure(
        setting="always", tools=[], caps={}, measured_mode="search"
    )
    assert decision.mode == "always"
    assert decision.error is None


def test_auto_measurement_is_recorded_in_the_decision() -> None:
    from src.sdk.mcp_exposure import resolve_exposure

    decision = resolve_exposure(
        setting="auto",
        tools=[],
        caps={},
        measured_mode="search",
        measurement_reason="definitions 42.0% of window >= 10%",
    )
    assert decision.measurement_reason == "definitions 42.0% of window >= 10%"


# --------------------------------------------------------------------------
# P4 — the decision must not be re-evaluated per LLM call
# --------------------------------------------------------------------------


def test_decision_is_stable_across_repeated_measurements() -> None:
    """P4: the same inputs must yield the same decision, or the surface churns."""
    tools = [_definition("t1"), _definition("t2")]
    results = {
        measure_mode(
            tools, model="openai:gpt-4o", threshold_pct=10.0, cache_warm=True
        )[0]
        for _ in range(5)
    }
    assert len(results) == 1
