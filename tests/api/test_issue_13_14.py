"""Issues #13/#14 regressions: custom-tool execution leg + optional placeholders."""

from __future__ import annotations

import asyncio

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

    from tests.sdk.sandbox_fakes import fake_sandbox

    tools = tc_mod.scan_tools_dir(tmp_path / "Tools")
    assert len(tools) == 1
    # The sandbox runs the command through Popen (#118); capture that seam.
    with fake_sandbox(results=((0, "ok", ""),)) as calls:
        asyncio.run(tools[0].ainvoke({"store": "acme"}))  # user omitted
    rendered = next(argv[-1] for argv, _timeout in calls if argv[:2] == ["sh", "-c"])
    assert "{{" not in rendered, "literal placeholder leaked into command"
    assert "acme" in rendered


@pytest.mark.asyncio
async def test_execute_approved_resolves_custom_tool(tmp_path, monkeypatch):
    """Issue #13: the execution leg resolves custom TOOL.md tools."""
    import src.sdk.governance as gov
    import src.storage.paths as paths_mod
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

    pid = svc.create_pending("u1", "snooze_execute", {"store": "acme"}, permission="ask")
    svc.approve("u1", pid)
    out = await svc.execute_approved("u1", pid, registry=None)

    assert out["structured_content"]["executed"] is True
    assert executed == ["acme"], "the custom tool must run via the execution leg"


@pytest.mark.asyncio
async def test_unknown_tool_still_marks_executed_with_error(tmp_path, monkeypatch):
    """Issue #13 evidence: unknown-tool result is is_error=True (surfaced),
    matching the shipped behavior the issue reported."""
    import src.sdk.governance as gov
    import src.storage.paths as paths_mod
    from src.sdk.governance import GovernanceService

    monkeypatch.setattr(
        paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
    )
    monkeypatch.setattr(gov, "_services", {})
    monkeypatch.setattr(gov, "governance_enabled", lambda: True)
    svc = GovernanceService()

    pid = svc.create_pending("u1", "ghost_tool", {}, permission="ask")
    svc.approve("u1", pid)
    out = await svc.execute_approved("u1", pid, registry=None)

    assert out["is_error"] is True
    assert "unknown tool" in out["structured_content"].get("error", "")
