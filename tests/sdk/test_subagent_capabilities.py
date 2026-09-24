"""Unit tests for strict subagent capability launch planning."""

from __future__ import annotations

from agentprofile.models import AgentProfile


def _profile(
    *,
    name: str = "worker",
    tools: list[str] | None = None,
    skills: list[str] | None = None,
) -> AgentProfile:
    return AgentProfile(
        name=name,
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
    user_id: str = "user",
    workspace_id: str = "personal",
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
        "memory_profile": ToolDefinition(
            name="memory_profile",
            description="Memory access",
            function=lambda: None,
            annotations=ToolAnnotations(read_only=True),
        ),
        "subagent_delegate": ToolDefinition(
            name="subagent_delegate",
            description="Recursive delegation",
            function=lambda: None,
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
    return capabilities.build_launch_plan(profile, user_id, workspace_id, mode)


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


def test_safe_default_rejects_declared_tool_outside_default_manifest(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    plan = _build_plan(
        monkeypatch,
        _profile(tools=["files_write"]),
        ToolSelectionMode.SAFE_DEFAULT,
    )
    assert plan.ready is False
    assert [(item.name, item.status) for item in plan.rejected_decisions] == [
        ("files_write", "not_in_safe_default")
    ]


def test_safe_default_rejects_declared_forbidden_tools(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    plan = _build_plan(
        monkeypatch,
        _profile(tools=["memory_profile"]),
        ToolSelectionMode.SAFE_DEFAULT,
    )
    assert plan.ready is False
    assert [(item.name, item.status) for item in plan.rejected_decisions] == [
        ("memory_profile", "forbidden")
    ]


def test_preflight_rejects_forbidden_tool_instead_of_silently_filtering(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    plan = _build_plan(
        monkeypatch,
        _profile(tools=["memory_profile", "subagent_delegate"]),
        ToolSelectionMode.ALLOWLIST,
    )

    assert plan.ready is False
    assert {(item.name, item.status) for item in plan.rejected_decisions} == {
        ("memory_profile", "forbidden"),
        ("subagent_delegate", "forbidden"),
    }


def test_safe_default_excludes_mutating_tools(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    plan = _build_plan(monkeypatch, _profile(), ToolSelectionMode.SAFE_DEFAULT)

    assert plan.ready is True
    assert plan.effective_tools == ("files_read", "skills_load")
    assert "files_write" not in plan.effective_tools


def test_legacy_empty_profile_currently_resolves_to_empty_manifest(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    plan = _build_plan(monkeypatch, _profile(), ToolSelectionMode.LEGACY)

    assert plan.ready is True
    assert plan.effective_tools == ()


def test_canonical_manifest_is_stable_across_mapping_and_name_order(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    first = _build_plan(
        monkeypatch,
        _profile(tools=["files_read", "skills_load"], skills=["research"]),
        ToolSelectionMode.ALLOWLIST,
        permissions={"files_read": "allow", "skills_load:research": "allow"},
    )
    second = _build_plan(
        monkeypatch,
        _profile(tools=["skills_load", "files_read"], skills=["research"]),
        ToolSelectionMode.ALLOWLIST,
        permissions={"skills_load:research": "allow", "files_read": "allow"},
    )

    assert first.plan_id == second.plan_id
    assert first.canonical_manifest_json == second.canonical_manifest_json


def test_canonical_manifest_id_changes_with_effective_capabilities_and_identity(monkeypatch) -> None:
    from src.sdk.subagent_capabilities import ToolSelectionMode

    baseline = _build_plan(
        monkeypatch,
        _profile(tools=["files_read"], skills=["research"]),
        ToolSelectionMode.ALLOWLIST,
    )
    variants = [
        _build_plan(monkeypatch, _profile(), ToolSelectionMode.NONE),
        _build_plan(
            monkeypatch,
            _profile(tools=["files_read"]),
            ToolSelectionMode.ALLOWLIST,
        ),
        _build_plan(
            monkeypatch,
            _profile(tools=["files_read"], skills=["research"]),
            ToolSelectionMode.LEGACY,
        ),
        _build_plan(
            monkeypatch,
            _profile(name="another-agent", tools=["files_read"], skills=["research"]),
            ToolSelectionMode.ALLOWLIST,
        ),
        _build_plan(
            monkeypatch,
            _profile(tools=["files_read"], skills=["research"]),
            ToolSelectionMode.ALLOWLIST,
            user_id="another-user",
        ),
        _build_plan(
            monkeypatch,
            _profile(tools=["files_read"], skills=["research"]),
            ToolSelectionMode.ALLOWLIST,
            workspace_id="another-workspace",
        ),
        _build_plan(
            monkeypatch,
            _profile(tools=["files_read"], skills=["research"]),
            ToolSelectionMode.ALLOWLIST,
            permissions={"files_read": "ask"},
        ),
    ]

    assert all(plan.plan_id != baseline.plan_id for plan in variants)
    assert variants[-1].ready is False


def test_canonical_manifest_contains_only_deterministic_decision_fields(monkeypatch) -> None:
    import hashlib
    import json

    from src.sdk.subagent_capabilities import ToolSelectionMode

    plan = _build_plan(
        monkeypatch,
        _profile(tools=["files_read"]),
        ToolSelectionMode.ALLOWLIST,
    )

    manifest = json.loads(plan.canonical_manifest_json)
    assert manifest["requested_workspace_id"] == "personal"
    assert manifest["effective_tools"] == ["files_read"]
    assert manifest["decisions"] == [
        {
            "destructive": False,
            "kind": "tool",
            "name": "files_read",
            "read_only": True,
            "status": "allowed",
        }
    ]
    assert "reason" not in manifest["decisions"][0]
    persisted = plan.to_persisted_dict()
    assert persisted["canonical_manifest"] == manifest
    assert plan.plan_id == hashlib.sha256(plan.canonical_manifest_json.encode()).hexdigest()
    assert len(plan.plan_id) == 64


def test_generic_skills_load_preflight_can_miss_item_level_runtime_ask(monkeypatch) -> None:
    from src.sdk.permission_policy import PermissionPolicy
    from src.sdk.subagent_capabilities import ToolSelectionMode

    plan = _build_plan(
        monkeypatch,
        _profile(),
        ToolSelectionMode.SAFE_DEFAULT,
        permissions={"skills_load:deployment": "ask"},
    )

    assert plan.ready is True
    assert "skills_load" in plan.effective_tools

    policy = PermissionPolicy(user={"skills": {"deployment": "ask"}})
    assert policy.resolve("skills_load", {}, fallback="allow") == "allow"
    assert policy.resolve(
        "skills_load", {"name": "deployment"}, fallback="allow"
    ) == "ask"
