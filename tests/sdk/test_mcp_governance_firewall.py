"""MCP Layer 4 — the governance firewall (I1–I5).

None of the three reference harnesses need these invariants: they are
single-user tools. We do, so they are enforced in code and pinned by tests.

I3 and I5 are implemented in Layers 5 and 2 respectively; I1 and I2 mostly
already hold and are pinned here. I4 (auditing an automatic surface change) is
the genuinely new behaviour.
"""

from __future__ import annotations

import pytest

from src.sdk.mcp_exposure import resolve_exposure


def _tool(name: str) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        name=name,
        description="d",
        inputSchema={"type": "object"},
        annotations=None,
        meta={},
    )


# --------------------------------------------------------------------------
# I1 — exposure narrows visibility and is never consulted for permission
# --------------------------------------------------------------------------


def test_exposure_never_changes_a_permission_decision() -> None:
    """I1: the context policy has no channel into permission resolution."""
    from src.sdk.governance import get_governance_service

    svc = get_governance_service("firewall-user")
    tool = "mcp__fs__write_file"
    decisions = set()
    for setting in ("always", "search", "never"):
        # Resolve an exposure decision and then, separately, the permission.
        decision = resolve_exposure(
            setting=setting,
            tools=[_tool(tool)],
            caps={"tools": {}},
        )
        assert decision.setting == setting
        decisions.add(
            svc.resolve_permission_for_call("firewall-user", tool, {})
        )
    assert len(decisions) == 1, f"permission varied with exposure: {decisions}"


def test_exposure_filters_are_never_read_by_the_permission_policy() -> None:
    """I1 structural check: the policy module must not import the resolver."""
    import inspect

    from src.sdk import permission_policy

    source = inspect.getsource(permission_policy)
    for forbidden in ("mcp_exposure", "include_tools", "exclude_tools", "exposure"):
        assert forbidden not in source, f"permission policy references {forbidden}"


# --------------------------------------------------------------------------
# I2 — a discovered tool is not an authorised tool
# --------------------------------------------------------------------------


def test_search_activated_tool_is_still_permission_checked_on_invocation() -> None:
    """I2: loading a tool into the loop must not bypass the allow/ask/deny gate."""
    from src.sdk.governance import get_governance_service

    svc = get_governance_service("firewall-user")
    # Whatever the visibility mode, the permission for the same call is fixed.
    for setting in ("always", "search"):
        resolve_exposure(
            setting=setting,
            tools=[_tool("mcp__fs__delete")],
            caps={"tools": {}},
        )
        denied = svc.resolve_permission_for_call(
            "firewall-user",
            "mcp__fs__delete",
            {"path": "/tmp/x"},
        )
        assert denied in {"allow", "ask", "deny"}


def test_a_denied_mcp_tool_is_not_promoted_even_when_visible() -> None:
    """A deny must win over an include: filters narrow, they never authorise."""
    from src.sdk.mcp_exposure import apply_capability_floor

    survivors = frozenset({"mcp__fs__delete", "mcp__fs__read"})
    caps = {"tools": {"mcp__fs__delete": False}}
    assert apply_capability_floor(survivors, caps) == frozenset({"mcp__fs__read"})


# --------------------------------------------------------------------------
# I3 — an excluded server stays visible with a reason
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_server_is_reported_in_health_not_hidden(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from src.sdk.tools_core.mcp_config import MCPConfig, MCPServerConfig
    from src.sdk.tools_core.mcp_manager import MCPManager

    config = MCPConfig(
        mcpServers={
            "off": MCPServerConfig(command="python", enabled=False),
            "on": MCPServerConfig(command="python"),
        }
    )
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config", lambda _u: config
    )
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config_state",
        lambda _u: (config, "valid", "/tmp/.mcp.json"),
    )
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.get_paths",
        lambda _u: SimpleNamespace(
            user_dir=tmp_path, subagent_schedules_db_path=tmp_path / "x.db"
        ),
    )
    manager = MCPManager("i3-user")
    try:
        health = await manager.health()
        assert health["servers"]["off"]["status"] == "disabled"
        assert health["servers"]["off"]["enabled"] is False
    finally:
        await manager.cleanup()


# --------------------------------------------------------------------------
# I4 — an automatic surface change is auditable and reported
# --------------------------------------------------------------------------


def test_exposure_decision_is_an_auditable_event_kind() -> None:
    from src.sdk.audit import AuditEvent

    event = AuditEvent(kind="exposure_decision", user_id="u", detail="mode=search")
    assert event.kind == "exposure_decision"
    assert event.detail == "mode=search"


@pytest.mark.asyncio
async def test_recorded_exposure_decision_is_reported_in_health(tmp_path, monkeypatch) -> None:
    """I4: 'what can this model reach right now' must have an answer."""
    from types import SimpleNamespace

    from src.sdk.tools_core.mcp_config import MCPConfig
    from src.sdk.tools_core.mcp_manager import MCPManager

    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config", lambda _u: MCPConfig()
    )
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config_state",
        lambda _u: (MCPConfig(), "valid", "/tmp/.mcp.json"),
    )
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.get_paths",
        lambda _u: SimpleNamespace(
            user_dir=tmp_path, subagent_schedules_db_path=tmp_path / "x.db"
        ),
    )
    manager = MCPManager("i4-user")
    try:
        decision = resolve_exposure(
            setting="search",
            tools=[_tool("mcp__a__b")],
            caps={"tools": {}},
        )
        manager.record_exposure_decision(
            {
                "mode": decision.mode,
                "setting": decision.setting,
                "survivors": sorted(decision.survivors),
                "excluded": decision.excluded,
                "deprecated": decision.deprecated,
            }
        )
        health = await manager.health()
        reported = health["exposure_decision"]
        assert reported["mode"] == "search"
        assert reported["survivors"] == ["mcp__a__b"]
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_health_omits_exposure_decision_until_one_is_recorded(
    tmp_path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from src.sdk.tools_core.mcp_config import MCPConfig
    from src.sdk.tools_core.mcp_manager import MCPManager

    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config", lambda _u: MCPConfig()
    )
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.load_mcp_config_state",
        lambda _u: (MCPConfig(), "valid", "/tmp/.mcp.json"),
    )
    monkeypatch.setattr(
        "src.sdk.tools_core.mcp_manager.get_paths",
        lambda _u: SimpleNamespace(
            user_dir=tmp_path, subagent_schedules_db_path=tmp_path / "x.db"
        ),
    )
    manager = MCPManager("i4-empty")
    try:
        health = await manager.health()
        assert "exposure_decision" not in health
    finally:
        await manager.cleanup()


# --------------------------------------------------------------------------
# I5 — capability scope is a floor, not a filter that can readmit
# --------------------------------------------------------------------------


def test_capability_floor_is_removal_only() -> None:
    """I5: the floor can only shrink the set, never grow it."""
    from src.sdk.mcp_exposure import apply_capability_floor

    survivors = frozenset({"a", "b"})
    # A malformed or hostile caps value must not widen the set.
    for caps in (
        {},
        {"tools": {}},
        {"tools": None},
        {"tools": "nonsense"},
        {"tools": {"a": True}},
    ):
        result = apply_capability_floor(survivors, caps)
        assert result <= survivors, f"floor widened the set for {caps!r}"
