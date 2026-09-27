"""A declared capability that cannot launch is reported at creation time.

A subagent profile can be created successfully with tools it can never use: the
launch preflight requires `allow`, and a tool defaulting to `ask` is refused at
*launch*. That is correct and fail-closed, but it leaves the operator to find
out at run time — which is how a deployment ends up adding a blanket
deployment-wide permission override as a workaround.

This adds a warning only. The gate is unchanged: unattended behaviour, the
firewall, and the scheduler's no-stall guarantee are all unaffected.
"""

from __future__ import annotations

import pytest


def _profile(tools: list[str]) -> object:
    from agentprofile.models import AgentProfile

    return AgentProfile(name="researcher", tools=tools)


def _caps() -> dict:
    return {"tools": {}}


def _warned(
    tools: list[str], *, permission: str, selection=None, monkeypatch=None
) -> list[str]:
    """Return the launch-blocked warnings a create/update would emit."""
    import src.sdk.subagent_capabilities as caps

    real = caps.resolve_permission
    if monkeypatch is not None:
        monkeypatch.setattr(
            caps, "resolve_permission", lambda _u, _n, _i: permission
        )
    try:
        mode = selection or caps.ToolSelectionMode.ALLOWLIST
        plan = caps.build_launch_plan(_profile(tools), "u", "personal", mode)
    finally:
        if monkeypatch is not None:
            monkeypatch.setattr(caps, "resolve_permission", real)
    return caps.launch_blockers(plan)


def test_a_tool_resolving_to_ask_is_reported_at_creation(monkeypatch) -> None:
    warnings = _warned(["shell_execute"], permission="ask", monkeypatch=monkeypatch)
    assert warnings, "an unlaunchable declared tool must be reported"
    assert "shell_execute" in warnings[0]
    assert "ask" in warnings[0]


def test_a_fully_authorised_profile_gets_no_warning(monkeypatch) -> None:
    assert _warned(["files_read"], permission="allow", monkeypatch=monkeypatch) == []


def test_a_denied_tool_is_reported_and_names_deny(monkeypatch) -> None:
    warnings = _warned(["files_read"], permission="deny", monkeypatch=monkeypatch)
    assert warnings
    assert "deny" in warnings[0]


def test_warnings_cover_every_blocking_tool(monkeypatch) -> None:
    warnings = _warned(
        ["shell_execute", "files_read"], permission="ask", monkeypatch=monkeypatch
    )
    assert len(warnings) == 2
    assert all("shell_execute" in w or "files_read" in w for w in warnings)


def test_warnings_say_the_launch_will_be_refused(monkeypatch) -> None:
    warnings = _warned(["shell_execute"], permission="ask", monkeypatch=monkeypatch)
    text = " ".join(warnings).casefold()
    assert "refused" in text or "cannot start" in text


def test_safe_default_mode_still_warns_when_the_launch_really_is_refused(
    monkeypatch,
) -> None:
    """Safe-default narrows the toolset; it does not make a refusal go away.

    A profile declaring an ask-gated tool fails preflight in safe-default mode
    too, so suppressing the warning here would be exactly the silent failure
    this change exists to remove.
    """
    warnings = _warned(
        ["shell_execute"],
        permission="ask",
        selection="safe_default",
        monkeypatch=monkeypatch,
    )
    assert warnings and "shell_execute" in warnings[0]


@pytest.mark.asyncio
async def test_subagent_create_warns_but_still_creates(tmp_path, monkeypatch) -> None:
    """The warning must not block creation; only launch is gated."""
    from src.sdk.tools_core.subagent import subagent_create

    created: list[str] = []

    class _Coord:
        def load_def(self, _name):
            return None

        async def create(self, profile, tool_selection_mode=None):
            created.append(profile.name)

    monkeypatch.setattr(
        "src.sdk.tools_core.subagent.get_coordinator", lambda *_a, **_k: _Coord()
    )
    monkeypatch.setattr(
        "src.sdk.subagent_capabilities.resolve_permission",
        lambda _u, _n, _i: "ask",
    )

    result = await subagent_create.ainvoke(
        {"name": "researcher", "user_id": "u", "tools": ["shell_execute"]}
    )

    assert created == ["researcher"], "creation must still succeed"
    assert "shell_execute" in result
    assert "ask" in result
