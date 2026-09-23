"""Unit tests for strict subagent capability launch planning."""

from __future__ import annotations

from agentprofile.models import AgentProfile


def _profile(*, tools: list[str] | None = None, skills: list[str] | None = None) -> AgentProfile:
    return AgentProfile(
        name="worker",
        tools=[] if tools is None else tools,
        skills=[] if skills is None else skills,
    )


def _build_plan(
    monkeypatch,
    profile: AgentProfile,
    mode: object,
    *,
    known_skills: set[str] | None = None,
    caps: dict[str, object] | None = None,
    permissions: dict[str, str] | None = None,
):
    from src.sdk import subagent_capabilities as capabilities
    from src.sdk.tools import ToolAnnotations, ToolDefinition

    tools = {
        "files_read": ToolDefinition(
            name="files_read",
            description="Read a file",
            function=lambda: None,
            annotations=ToolAnnotations(read_only=True, idempotent=True),
        ),
        "files_write": ToolDefinition(
            name="files_write",
            description="Write a file",
            function=lambda: None,
            annotations=ToolAnnotations(destructive=True),
        ),
        "skills_load": ToolDefinition(
            name="skills_load",
            description="Load a skill",
            function=lambda: None,
            annotations=ToolAnnotations(read_only=True, idempotent=True),
        ),
    }
    known_skills = {"research"} if known_skills is None else known_skills
    caps = caps or {"tools": {}, "skills": {}, "subagents": {}}
    permissions = permissions or {}
    monkeypatch.setattr(capabilities, "get_native_tools", lambda: list(tools.values()))
    monkeypatch.setattr(capabilities, "load_user_capabilities", lambda _user_id: caps)
    monkeypatch.setattr(
        capabilities,
        "get_skill_registry",
        lambda **_kwargs: type(
            "Registry",
            (),
            {"get_skill": lambda _self, name: {"name": name} if name in known_skills else None},
        )(),
    )
    monkeypatch.setattr(
        capabilities,
        "resolve_permission",
        lambda _user_id, tool_name, tool_input: permissions.get(
            f"{tool_name}:{tool_input.get('name')}", permissions.get(tool_name, "allow")
        ),
    )
    return capabilities.build_launch_plan(profile, "user", "personal", mode)


def test_allowlist_preflight_resolves_declared_read_tool_and_skill(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    plan = _build_plan(
        monkeypatch,
        _profile(tools=["files_read"], skills=["research"]),
        ToolSelectionMode.ALLOWLIST,
    )

    assert plan.ready is True
    assert plan.effective_tools == ("files_read", "skills_load")
    assert plan.effective_skills == ("research",)
    assert plan.rejected_decisions == ()


def test_preflight_reports_missing_declared_tool(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    plan = _build_plan(
        monkeypatch,
        _profile(tools=["does_not_exist"]),
        ToolSelectionMode.ALLOWLIST,
    )

    assert plan.ready is False
    assert [(item.name, item.status) for item in plan.rejected_decisions] == [
        ("does_not_exist", "missing")
    ]


def test_preflight_reports_disabled_declared_tool(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    plan = _build_plan(
        monkeypatch,
        _profile(tools=["files_read"]),
        ToolSelectionMode.ALLOWLIST,
        caps={"tools": {"files_read": False}, "skills": {}, "subagents": {}},
    )

    assert plan.ready is False
    assert [(item.name, item.status) for item in plan.rejected_decisions] == [
        ("files_read", "disabled")
    ]


def test_preflight_reports_missing_or_disabled_skills(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    missing = _build_plan(
        monkeypatch,
        _profile(skills=["missing-skill"]),
        ToolSelectionMode.NONE,
    )
    assert [(item.name, item.status) for item in missing.rejected_decisions] == [
        ("missing-skill", "missing")
    ]

    disabled = _build_plan(
        monkeypatch,
        _profile(skills=["research"]),
        ToolSelectionMode.NONE,
        caps={"tools": {}, "skills": {"research": False}, "subagents": {}},
    )
    assert [(item.name, item.status) for item in disabled.rejected_decisions] == [
        ("research", "disabled")
    ]


def test_preflight_rejects_ask_and_deny_permissions(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    plan = _build_plan(
        monkeypatch,
        _profile(tools=["files_read"], skills=["research"]),
        ToolSelectionMode.ALLOWLIST,
        permissions={
            "files_read": "ask",
            "skills_load:research": "allow",
            "skills_load": "deny",
        },
    )

    assert plan.ready is False
    assert {(item.name, item.status) for item in plan.rejected_decisions} == {
        ("files_read", "permission_ask"),
        ("skills_load", "permission_deny"),
    }


def test_safe_default_excludes_mutating_tools(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    plan = _build_plan(monkeypatch, _profile(), ToolSelectionMode.SAFE_DEFAULT)

    assert plan.ready is True
    assert plan.effective_tools == ("files_read", "skills_load")
    assert "files_write" not in plan.effective_tools
