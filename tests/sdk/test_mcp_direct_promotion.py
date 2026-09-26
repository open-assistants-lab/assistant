"""MCP Layer 2 — which tools may be direct (Axis B).

One axis, borrowed from Pi's ladder plus OpenCode's cheap disable, with the
resolution order spelled out so it can be tested rather than inferred.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"{name} description",
        inputSchema={"type": "object"},
        annotations=None,
        meta={},
    )


# --------------------------------------------------------------------------
# glob matching
# --------------------------------------------------------------------------


def test_glob_matches_star_and_question_mark() -> None:
    from src.sdk.mcp_exposure import matches_any

    assert matches_any("mcp__github__create_issue", ["mcp__github*"]) is True
    assert matches_any("mcp__github__create_issue", ["mcp__github*_issue"]) is True
    assert matches_any("mcp__github__create_issue", ["mcp__gitlab*"]) is False
    assert matches_any("abc", ["a?c"]) is True
    assert matches_any("abbc", ["a?c"]) is False
    # literal when no wildcard is present
    assert matches_any("files_read", ["files_read"]) is True
    assert matches_any("files_read", ["files_write"]) is False
    assert matches_any("anything", []) is False


# --------------------------------------------------------------------------
# include / exclude precedence and scoping
# --------------------------------------------------------------------------


def test_include_then_exclude_precedence() -> None:
    """Pi applies excludeTools *after* includeTools; a name in both is excluded."""
    from src.sdk.mcp_exposure import filter_survivors

    tools = [_tool("get_apps"), _tool("get_dbs"), _tool("delete_everything")]
    survivors, excluded = filter_survivors(
        tools,
        include=("get_*",),
        exclude=("get_dbs",),
    )
    assert {t.name for t in survivors} == {"get_apps"}
    assert excluded["delete_everything"] == "not included"
    assert excluded["get_dbs"] == "excluded"


def test_empty_include_means_everything() -> None:
    from src.sdk.mcp_exposure import filter_survivors

    tools = [_tool("a"), _tool("b")]
    survivors, excluded = filter_survivors(tools, include=(), exclude=("b",))
    assert {t.name for t in survivors} == {"a"}
    assert excluded == {"b": "excluded"}


def test_server_level_filters_narrow_global_filters() -> None:
    """Per-server include narrows; it can never re-admit a globally excluded tool."""
    from src.sdk.mcp_exposure import filter_survivors

    tools = [_tool("a"), _tool("b")]
    survivors, excluded = filter_survivors(
        tools,
        include=("a", "b"),
        exclude=(),
        server_exclude=("a",),
    )
    assert {t.name for t in survivors} == {"b"}
    assert excluded["a"] == "excluded by server"


# --------------------------------------------------------------------------
# the coarse tools-layer gate (OpenCode glob disable)
# --------------------------------------------------------------------------


def test_tools_disabled_globs_remove_a_whole_family() -> None:
    from src.sdk.mcp_exposure import filter_survivors

    tools = [_tool("mcp__github__a"), _tool("mcp__github__b"), _tool("mcp__slack__c")]
    survivors, excluded = filter_survivors(
        tools, include=(), exclude=(), disabled_globs=("mcp__github*",)
    )
    assert {t.name for t in survivors} == {"mcp__slack__c"}
    assert excluded["mcp__github__a"] == "disabled by tools configuration"


# --------------------------------------------------------------------------
# exposure setting resolution, including the legacy mapping
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ("always", "always"),
        ("search", "search"),
        ("never", "never"),
        # v0.6.21 legacy values
        ("direct", "always"),
        ("hybrid", "search"),
        ("proxy", "never"),
    ],
)
def test_exposure_setting_resolves_including_legacy_values(setting, expected) -> None:
    from src.sdk.mcp_exposure import resolve_mode

    decision = resolve_mode(setting)
    assert decision.mode == expected
    assert decision.deprecated is (setting in {"direct", "hybrid", "proxy"})


def test_unknown_exposure_value_fails_closed() -> None:
    """Never default an unrecognised value to something permissive (#44's shape)."""
    from src.sdk.mcp_exposure import resolve_mode

    decision = resolve_mode("definitely-not-a-mode")
    assert decision.mode == "never"
    assert decision.error is not None
    assert "definitely-not-a-mode" in decision.error


# --------------------------------------------------------------------------
# always_load — operator only unless server trust is explicit (C3)
# --------------------------------------------------------------------------


def test_operator_always_load_survives_filtering() -> None:
    from src.sdk.mcp_exposure import resolve_always_load

    tools = [_tool("health"), _tool("other")]
    chosen = resolve_always_load(
        tools, operator=("health",), server_trust=False
    )
    assert chosen == frozenset({"health"})


def test_server_always_load_needs_explicit_trust() -> None:
    from src.sdk.mcp_exposure import resolve_always_load

    tools = [_tool("health", meta={"alwaysLoad": True})]
    _tool("health", meta={"alwaysLoad": True})
    untrusted = resolve_always_load(tools, operator=(), server_trust=False)
    assert untrusted == frozenset()
    trusted = resolve_always_load(tools, operator=(), server_trust=True)
    assert trusted == frozenset({"health"})


def _tool(name: str, meta: dict | None = None) -> SimpleNamespace:  # noqa: F811
    return SimpleNamespace(
        name=name,
        description=f"{name} description",
        inputSchema={"type": "object"},
        annotations=None,
        meta=meta or {},
    )


# --------------------------------------------------------------------------
# I5 — the governance floor
# --------------------------------------------------------------------------


def test_capability_disabled_tools_are_dropped_and_never_readmitted() -> None:
    """A filter that removes from the active set must not readmit to context."""
    from src.sdk.mcp_exposure import apply_capability_floor

    survivors = frozenset({"mcp__fs__read", "mcp__fs__delete"})
    caps = {"tools": {"mcp__fs__delete": False}}

    result = apply_capability_floor(survivors, caps)

    assert result == frozenset({"mcp__fs__read"})


def test_capability_floor_keeps_unconfigured_tools() -> None:
    from src.sdk.mcp_exposure import apply_capability_floor

    survivors = frozenset({"mcp__fs__read"})
    assert apply_capability_floor(survivors, {}) == survivors


# --------------------------------------------------------------------------
# end-to-end decision
# --------------------------------------------------------------------------


def test_full_decision_reports_mode_survivors_and_reasons() -> None:
    from src.sdk.mcp_exposure import resolve_exposure

    tools = [
        _tool("mcp__github__create_issue"),
        _tool("mcp__github__delete_repo"),
        _tool("mcp__slack__post"),
    ]
    decision = resolve_exposure(
        setting="search",
        tools=tools,
        include=(),
        exclude=("mcp__github__delete_repo",),
        disabled_globs=(),
        caps={"tools": {}},
        operator_always_load=(),
        server_trust=False,
    )

    assert decision.mode == "search"
    assert decision.survivors == frozenset(
        {"mcp__github__create_issue", "mcp__slack__post"}
    )
    assert decision.excluded["mcp__github__delete_repo"] == "excluded"
    assert decision.deprecated is False
    assert decision.error is None


def test_decision_reports_legacy_deprecation_for_health() -> None:
    from src.sdk.mcp_exposure import resolve_exposure

    decision = resolve_exposure(
        setting="direct",
        tools=[_tool("mcp__x__y")],
        include=(),
        exclude=(),
        disabled_globs=(),
        caps={},
        operator_always_load=(),
        server_trust=False,
    )
    assert decision.mode == "always"
    assert decision.deprecated is True


def test_never_mode_has_no_survivors_reaching_the_context() -> None:
    """In `never`, nothing is promoted regardless of filters."""
    from src.sdk.mcp_exposure import resolve_exposure

    decision = resolve_exposure(
        setting="never",
        tools=[_tool("mcp__x__y")],
        include=(),
        exclude=(),
        disabled_globs=(),
        caps={},
        operator_always_load=(),
        server_trust=False,
    )
    assert decision.mode == "never"
    assert decision.survivors == frozenset()
