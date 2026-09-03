"""Issues #13/#14 regressions: custom-tool execution leg + optional placeholders."""

from __future__ import annotations

import asyncio

from pathlib import Path

import pytest


def test_unfilled_optional_placeholders_render_empty(tmp_path, monkeypatch):
    """Issue #14: omitted optional params must not send literal {{param}}."""
    import src.sdk.tools_custom as tc_mod

    tools_dir = tmp_path / "Tools" / "lookup"
    tools_dir.mkdir(parents=True)
    (tools_dir / "TOOL.md").write_text(
        "---\nname: lookup\ndescription: lookup tool\ncommand: curl --data-urlencode user={{user}} store={{store}}\n---\nbody",
        encoding="utf-8",
    )

    fake_argv: list[list[str]] = []

    def fake_run(argv, **kw):
        fake_argv.append(argv)

        class R:
            returncode = 0
            stdout = b"ok"
            stderr = b""

        return R()

    import subprocess as _sp

    orig_run = _sp.run
    _sp.run = fake_run
    try:
        tools = tc_mod.scan_tools_dir(tmp_path / "Tools")
        assert len(tools) == 1
        asyncio.run(tools[0].ainvoke({"store": "acme"}))  # user omitted
        rendered = str(fake_argv[-1])
        assert "{{" not in rendered, "literal placeholder leaked into command"
        assert "acme" in rendered
    finally:
        _sp.run = orig_run


@pytest.mark.asyncio
async def test_execute_approved_resolves_custom_tool(tmp_path, monkeypatch):
    """Issue #13: the execution leg resolves custom TOOL.md tools."""
    import src.storage.paths as paths_mod
    import src.sdk.governance as gov
    from src.sdk.governance import GovernanceService
    from src.sdk.tools import tool

    monkeypatch.setattr(
        paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
    )
    monkeypatch.setattr(gov, "_services", {})
    monkeypatch.setattr(gov, "governance_enabled", lambda: True)
    svc = GovernanceService()

    executed: list[str] = []

    @tool(name="snooze_execute")
    async def snooze_execute(store: str = "") -> str:
        """Custom-style tool (as a TOOL.md scan result would register)."""
        executed.append(store)
        return "SNOOZED"

    # The custom scan returns the same tool (simulating the user's Tools/).
    import src.sdk.tools_custom as tc_mod

    monkeypatch.setattr(tc_mod, "get_custom_tools", lambda user_id: [snooze_execute])

    pid = svc.create_pending("u1", "snooze_execute", {"store": "acme"}, tier="explicit")
    svc.approve("u1", pid)
    out = await svc.execute_approved("u1", pid, registry=None)

    assert out["structured_content"]["executed"] is True
    assert executed == ["acme"], "the custom tool must run via the execution leg"


@pytest.mark.asyncio
async def test_unknown_tool_still_marks_executed_with_error(tmp_path, monkeypatch):
    """Issue #13 evidence: unknown-tool result is is_error=True (surfaced),
    matching the shipped behavior the issue reported."""
    import src.storage.paths as paths_mod
    import src.sdk.governance as gov
    from src.sdk.governance import GovernanceService

    monkeypatch.setattr(
        paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
    )
    monkeypatch.setattr(gov, "_services", {})
    monkeypatch.setattr(gov, "governance_enabled", lambda: True)
    svc = GovernanceService()

    pid = svc.create_pending("u1", "ghost_tool", {}, tier="explicit")
    svc.approve("u1", pid)
    out = await svc.execute_approved("u1", pid, registry=None)

    assert out["is_error"] is True
    assert "unknown tool" in out["structured_content"].get("error", "")