"""Approval must resolve the same current definition as the live runner (#51)."""

from types import SimpleNamespace

import pytest

from src.sdk.governance import GovernanceService
from src.sdk.tools import ToolDefinition


@pytest.fixture
async def approval_catalog(tmp_path, monkeypatch):
    import src.sdk.capabilities as caps
    import src.sdk.deployment_tools as policy
    import src.sdk.native_tools as native
    import src.sdk.runner as runner
    import src.sdk.tools_custom as custom
    import src.storage.paths as paths

    monkeypatch.setattr(paths.DataPaths, "root", property(lambda self: tmp_path))
    settings = SimpleNamespace(tools=SimpleNamespace(native=SimpleNamespace(mode="all")))
    monkeypatch.setattr(policy, "get_settings", lambda: settings)
    monkeypatch.setattr(runner, "get_settings", lambda: settings)
    monkeypatch.setattr(caps, "load_capabilities", lambda root: {})
    monkeypatch.setattr(runner, "_load_user_capabilities", lambda user: {})
    calls = []

    async def shipped() -> str:
        calls.append("native")
        return "native"

    async def override() -> str:
        calls.append("custom")
        return "custom"

    schema = {"type": "object", "properties": {}}
    td = ToolDefinition(name="gated", description="native", parameters=schema, function=shipped)
    replacement = ToolDefinition(
        name="gated", description="custom", parameters=schema, function=override
    )
    monkeypatch.setattr(native, "get_native_tools", lambda: [td])
    monkeypatch.setattr(runner, "get_native_tools", lambda: [td])
    monkeypatch.setattr(custom, "get_custom_tools", lambda user: [])
    svc = GovernanceService()
    monkeypatch.setattr(svc, "resolve_permission", lambda *args: "ask")
    yield svc, settings, calls, td, replacement
    for kernel in svc._execution_kernels.values():
        await kernel._store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_registry", [False, True])
async def test_deployment_deny_after_proposal_blocks_execution(approval_catalog, explicit_registry):
    svc, settings, calls, td, _ = approval_catalog
    pid = svc.create_pending("alice", "gated", {}, permission="ask")
    svc.approve("alice", pid)
    settings.tools.native.mode = "none"
    result = await svc.execute_approved("alice", pid, registry=[td] if explicit_registry else None)
    assert calls == []
    assert result["is_error"]
    assert result["structured_content"]["executed"] is False


@pytest.mark.asyncio
async def test_custom_collision_matches_live_resolver(approval_catalog, monkeypatch):
    import src.sdk.runner as runner
    import src.sdk.tools_custom as custom

    svc, _, calls, _, replacement = approval_catalog
    monkeypatch.setattr(custom, "get_custom_tools", lambda user: [replacement])
    assert runner.get_active_tool_definition("alice", "gated") is replacement
    pid = svc.create_pending("alice", "gated", {}, permission="ask")
    svc.approve("alice", pid)
    result = await svc.execute_approved("alice", pid)
    assert calls == ["custom"]
    assert result["content"] == "custom"


@pytest.mark.asyncio
async def test_catalog_failure_does_not_fall_back_to_native(approval_catalog, monkeypatch):
    import src.sdk.tools_custom as custom

    svc, _, calls, _, _ = approval_catalog
    pid = svc.create_pending("alice", "gated", {}, permission="ask")
    svc.approve("alice", pid)

    def broken(user: str) -> list:
        raise OSError("catalog unavailable")

    monkeypatch.setattr(custom, "get_custom_tools", broken)
    result = await svc.execute_approved("alice", pid)
    assert calls == []
    assert result["is_error"]
