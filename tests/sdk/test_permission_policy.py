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


def test_shell_defaults_to_ask(tmp_path) -> None:
    service = GovernanceService(data_root=tmp_path)

    assert service._default_permission("shell_execute") == "ask"


def test_user_allow_cannot_weaken_shell_ask(monkeypatch, tmp_path) -> None:
    import src.sdk.capabilities as capabilities

    monkeypatch.setattr(
        capabilities,
        "load_capabilities",
        lambda _root: {"permissions": {"tools": {"shell_execute": "allow"}}},
    )
    service = GovernanceService(data_root=tmp_path)

    assert service.resolve_permission_for_call("user", "shell_execute", {}) == "ask"


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

    assert service.resolve_permission_for_call("user", "files_read", {}) == "ask"


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
        assert service.resolve_permission_for_call("user", "email_send", {}) == "deny"
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
        assert service.resolve_permission_for_call("user", "files_read", {}) == "ask"
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
    assert result.structured_content["governance"] == "ask"
    assert result.structured_content["status"] == "pending"
    assert len(service.list_pending_ids("user")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skill_name", "permission", "expected_provider_calls"),
    [("deployment", "ask", 1), ("restricted", "deny", 2)],
)
async def test_child_runtime_ask_or_deny_never_executes_skill_tool(
    monkeypatch, tmp_path, skill_name, permission, expected_provider_calls
):
    import src.sdk.capabilities as capabilities
    import src.sdk.governance as governance
    from src.sdk.loop import AgentLoop, RunConfig
    from src.sdk.messages import Message, ToolCall
    from src.sdk.providers.base import LLMProvider, ModelInfo
    from src.sdk.subagent_context import SubagentContext
    from src.sdk.tools import ToolAnnotations, ToolDefinition

    monkeypatch.setattr(
        capabilities,
        "load_capabilities",
        lambda _root: {"permissions": {"skills": {"deployment": "ask", "restricted": "deny"}}},
    )
    service = GovernanceService(data_root=tmp_path)
    monkeypatch.setattr(governance, "governance_enabled", lambda: True)
    monkeypatch.setattr(governance, "get_governance_service", lambda _user: service)
    assert service.resolve_permission_for_call("user", "skills_load", {}) == "allow"
    assert service.resolve_permission_for_call(
        "user", "skills_load", {"name": skill_name}
    ) == permission

    tool_calls = 0

    async def load_skill(name: str, user_id: str = "user") -> str:
        nonlocal tool_calls
        tool_calls += 1
        return f"loaded {name} for {user_id}"

    skill_tool = ToolDefinition(
        name="skills_load",
        description="Load a named skill",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}, "user_id": {"type": "string"}},
            "required": ["name"],
        },
        annotations=ToolAnnotations(read_only=True),
        function=load_skill,
    )

    class Provider(LLMProvider):
        provider_id = "test"

        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None, model=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return Message.assistant(
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            name="skills_load",
                            arguments={"name": skill_name},
                        )
                    ]
                )
            return Message.assistant("The task is complete.")

        def chat_stream(self, *args, **kwargs):
            async def empty_stream():
                if False:
                    yield None
            return empty_stream()

        def count_tokens(self, text, model=None):
            return len(text)

        def get_model_info(self, model):
            return ModelInfo(id=model, provider_id=self.provider_id)

    context = SubagentContext()
    provider = Provider()
    loop = AgentLoop(
        provider=provider,
        tools=[skill_tool],
        middlewares=[HITLMiddleware(user_id="user", subagent_context=context)],
        run_config=RunConfig(max_iterations=3),
        user_id="user",
    )
    loop.subagent_ctx = context

    await loop.run([Message.user("Load the skill.")])

    assert tool_calls == 0
    assert provider.calls == expected_provider_calls
    assert service.list_pending_ids("user") == []
    if permission == "ask":
        assert context.runtime_block is not None
        assert context.runtime_block.error_code == "approval_required"
    else:
        assert context.runtime_block is None
