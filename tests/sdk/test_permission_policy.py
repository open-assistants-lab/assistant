import pytest

from src.config import reload_settings
from src.sdk import PermissionPolicy
from src.sdk.governance import GovernanceService
from src.sdk.middleware_hitl import HITLMiddleware


def test_permission_policy_resolves_tool_and_item_rules() -> None:
    policy = PermissionPolicy(
        admin={"tools": {"email_send": "allow"}},
        user={"tools": {"email_send": "ask"}},
    )

    assert policy.resolve("email_send", {}, fallback="allow") == "ask"


def test_permission_policy_deny_wins_over_allow() -> None:
    policy = PermissionPolicy(
        admin={"tools": {"email_send": "deny"}},
        user={"tools": {"email_send": "allow"}},
    )

    assert policy.resolve("email_send", {}, fallback="allow") == "deny"


def test_user_allow_cannot_weaken_existing_restriction() -> None:
    policy = PermissionPolicy(user={"tools": {"files_delete": "allow"}})

    assert policy.resolve("files_delete", {}, fallback="ask") == "ask"


def test_skill_and_subagent_rules_match_call_arguments() -> None:
    policy = PermissionPolicy(
        user={
            "skills": {"deployment": "ask"},
            "subagents": {"production-ops": "deny"},
        }
    )

    assert policy.resolve("skills_load", {"name": "deployment"}, fallback="allow") == "ask"
    assert (
        policy.resolve(
            "subagent_start", {"agent_name": "production-ops"}, fallback="allow"
        )
        == "deny"
    )


def test_user_can_require_hitl_for_a_tool(monkeypatch, tmp_path) -> None:
    import src.sdk.capabilities as capabilities

    monkeypatch.setattr(
        capabilities,
        "load_capabilities",
        lambda _root: {"permissions": {"tools": {"files_read": "ask"}}},
    )
    service = GovernanceService(data_root=tmp_path)

    assert service.resolve_tier_for_call("user", "files_read", {}) == "explicit"


def test_admin_deny_wins_over_user_allow(monkeypatch, tmp_path) -> None:
    import src.sdk.capabilities as capabilities

    monkeypatch.setenv(
        "GOVERNANCE_PERMISSIONS",
        '{"tools":{"email_send":"deny"}}',
    )
    monkeypatch.setattr(
        capabilities,
        "load_capabilities",
        lambda _root: {"permissions": {"tools": {"email_send": "allow"}}},
    )
    reload_settings()
    try:
        service = GovernanceService(data_root=tmp_path)
        assert service.resolve_tier_for_call("user", "email_send", {}) == "hard_block"
    finally:
        monkeypatch.delenv("GOVERNANCE_PERMISSIONS")
        reload_settings()


def test_user_can_make_admin_allow_stricter(monkeypatch, tmp_path) -> None:
    import src.sdk.capabilities as capabilities

    monkeypatch.setenv(
        "GOVERNANCE_PERMISSIONS",
        '{"tools":{"files_read":"allow"}}',
    )
    monkeypatch.setattr(
        capabilities,
        "load_capabilities",
        lambda _root: {"permissions": {"tools": {"files_read": "ask"}}},
    )
    reload_settings()
    try:
        service = GovernanceService(data_root=tmp_path)
        assert service.resolve_tier_for_call("user", "files_read", {}) == "explicit"
    finally:
        monkeypatch.delenv("GOVERNANCE_PERMISSIONS")
        reload_settings()


@pytest.mark.asyncio
async def test_middleware_applies_skill_permission(monkeypatch, tmp_path) -> None:
    import src.sdk.capabilities as capabilities
    import src.sdk.governance as governance

    monkeypatch.setattr(
        capabilities,
        "load_capabilities",
        lambda _root: {"permissions": {"skills": {"deployment": "ask"}}},
    )
    service = GovernanceService(data_root=tmp_path)
    monkeypatch.setattr(governance, "governance_enabled", lambda: True)
    monkeypatch.setattr(governance, "get_governance_service", lambda _user: service)

    result = await HITLMiddleware(user_id="user").guard_tool_call(
        "skills_load", {"name": "deployment"}
    )

    assert result is not None
    assert result.structured_content is not None
    assert result.structured_content["governance"] == "explicit"
    assert result.structured_content["status"] == "pending"
