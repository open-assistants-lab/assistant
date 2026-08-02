from src.sdk import runner
from src.sdk.messages import Message
from src.skills import registry as skills_registry


class FakeRegistry:
    def __init__(self, skills):
        self._skills = skills
        self._load_counts: dict[str, int] = {}

    def get_all_skills(self):
        return self._skills

    def get_load_count(self, name: str) -> int:
        return self._load_counts.get(name, 0)


def test_get_skills_context_uses_user_registry(monkeypatch):
    calls = []

    def fake_get_skill_registry(**kwargs):
        calls.append(kwargs)
        return FakeRegistry([
            {
                "name": "project-helper",
                "description": "Project-specific instructions.",
                "content": "FULL CONTENT SHOULD NOT BE INCLUDED",
                "metadata": {"scope": "workspace"},
            }
        ])

    monkeypatch.setattr(skills_registry, "get_skill_registry", fake_get_skill_registry)

    context = runner._get_skills_context("u", "ws1")

    assert calls == [{"user_id": "u"}]
    assert "project-helper" in context
    assert "Project-specific instructions." in context
    assert "FULL CONTENT SHOULD NOT BE INCLUDED" not in context
    assert "scope" not in context.lower()
    assert "(workspace)" not in context.lower()


def test_get_system_prompt_uses_user_level_skills_context(monkeypatch):
    calls = []

    def fake_get_skill_registry(**kwargs):
        calls.append(kwargs)
        return FakeRegistry([
            {"name": "ws-skill", "description": "Skill from ws1.", "metadata": {}}
        ])

    monkeypatch.setattr(skills_registry, "get_skill_registry", fake_get_skill_registry)

    prompt = runner._get_system_prompt("u", "ws1")

    assert calls == [{"user_id": "u"}]
    assert "ws-skill" in prompt


def test_get_system_prompt_does_not_include_workspace_context(monkeypatch):
    def fail_workspace_context(workspace_id):
        raise AssertionError("workspace context must not be used in cached loop prompts")

    monkeypatch.setattr(runner, "_get_workspace_context", fail_workspace_context)
    monkeypatch.setattr(runner, "_get_skills_context", lambda user_id: "user skills")
    monkeypatch.setattr(runner, "_get_connector_context", lambda user_id: "")

    prompt = runner._get_system_prompt("u", "ws1")

    assert "user skills" in prompt
    assert "Current Workspace" not in prompt


def test_get_skills_context_excludes_disabled_skills(monkeypatch):
    def fake_get_skill_registry(**kwargs):
        return FakeRegistry([
            {"name": "visible", "description": "Visible skill.", "metadata": {}},
            {
                "name": "disabled-true",
                "description": "Disabled true.",
                "metadata": {"disable_model_invocation": "true"},
            },
            {
                "name": "disabled-one",
                "description": "Disabled one.",
                "metadata": {"disable_model_invocation": "1"},
            },
            {
                "name": "disabled-yes",
                "description": "Disabled yes.",
                "metadata": {"disable_model_invocation": "yes"},
            },
        ])

    monkeypatch.setattr(skills_registry, "get_skill_registry", fake_get_skill_registry)

    context = runner._get_skills_context("u")

    assert "visible" in context
    assert "disabled-true" not in context
    assert "disabled-one" not in context
    assert "disabled-yes" not in context


async def test_run_sdk_agent_stream_does_not_mutate_system_message(monkeypatch):
    recorded = []
    flow = {}

    class FakeLoop:
        model_id = "openai:gpt-4.1"
        state = None
        rubric = None
        cancel_event = None

        async def run_stream(self, messages):
            recorded.extend(messages)
            flow.update(
                user_id=self._flow_user_id,
                session_id=self._flow_session_id,
                model=self._flow_model,
                attempt=self._flow_attempt,
            )
            if False:
                yield None

    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()

    monkeypatch.setattr(runner, "get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr(
        runner,
        "_get_workspace_context",
        lambda workspace_id: "\n\n## Current Workspace: X",
    )

    chunks = runner.run_sdk_agent_stream(
        user_id="u",
        messages=[Message.system("base")],
        workspace_id="ws1",
        model="ignored-request-model",
        session_id="chat-1",
    )
    async for _ in chunks:
        pass

    assert recorded[0].content == "base"
    assert flow == {
        "user_id": "u",
        "session_id": "chat-1",
        "model": "openai:gpt-4.1",
        "attempt": 1,
    }


async def test_run_sdk_agent_sets_canonical_flow_fields_before_invocation(monkeypatch):
    flow = {}

    class FakeLoop:
        model_id = "openrouter:anthropic/claude-sonnet-4"
        state = None
        rubric = None

        async def run(self, messages):
            flow.update(
                user_id=self._flow_user_id,
                session_id=self._flow_session_id,
                model=self._flow_model,
                attempt=self._flow_attempt,
            )
            return messages

    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()

    monkeypatch.setattr(runner, "get_sdk_loop", fake_get_sdk_loop)
    monkeypatch.setattr(runner, "_persist_run_outcome", lambda *args, **kwargs: _noop())

    await runner.run_sdk_agent(
        user_id="u",
        messages=[Message.user("hello")],
        model="ignored-request-model",
        session_id=None,
    )

    assert flow == {
        "user_id": "u",
        "session_id": "default",
        "model": "openrouter:anthropic/claude-sonnet-4",
        "attempt": 1,
    }


async def _noop():
    return None


async def test_get_sdk_loop_reuses_provider_key_loop_for_runtime_state(monkeypatch):
    runner._loop_cache.clear()
    created = []

    async def fake_create_sdk_loop(*args, **kwargs):
        loop = object()
        created.append(loop)
        return loop

    monkeypatch.setattr(runner, "create_sdk_loop", fake_create_sdk_loop)

    keys = {"openai": "test-key"}
    first = await runner.get_sdk_loop("u", "ws", model="openai:gpt-4.1", provider_keys=keys)
    second = await runner.get_sdk_loop("u", "ws", model="openai:gpt-4.1", provider_keys=keys)

    assert second is first
    assert created == [first]


async def test_create_sdk_loop_uses_user_level_runtime_context(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, patch

    from src.sdk.tools import ToolDefinition

    class FakePaths:
        @property
        def root(self):
            return tmp_path

        def user_tools_dir(self):
            return tmp_path / "Tools"

        def workspace_tools_dir(self):
            raise AssertionError("workspace tools must not be used by cached loop runtime")

        def user_mcp_config(self):
            return tmp_path / ".mcp.json"

    class FakeIndex:
        def count(self):
            return 0

        def clear(self):
            pass

        def index_tool(self, *args, **kwargs):
            pass

    seen_prompt_args = []

    with (
        patch("src.sdk.runner.get_settings") as mock_settings,
        patch("src.sdk.runner.create_model_from_config") as mock_create_provider,
        patch(
            "src.sdk.runner.get_native_tools",
            return_value=[ToolDefinition(name="demo_lookup", description="Lookup", parameters={}, function=lambda: "ok")],
        ),
        patch("src.sdk.runner._seed_default_workspace"),
        patch(
            "src.sdk.runner._get_system_prompt",
            side_effect=lambda user_id, workspace_id=None: seen_prompt_args.append((user_id, workspace_id)) or "prompt",
        ),
        patch("src.storage.paths.get_paths", return_value=FakePaths()),
        patch("src.sdk.tool_index.get_or_create_index", return_value=FakeIndex()),
        patch("src.sdk.tools_custom.scan_tools_dir", return_value=[]),
    ):
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = False
        settings.agent.model = "ollama:test-model"
        mock_provider = AsyncMock()
        mock_provider.provider_id = "ollama"
        mock_provider.model = "ollama:test-model"
        mock_create_provider.return_value = mock_provider

        loop = await runner.create_sdk_loop(user_id="u", workspace_id="ws1")

    assert loop.workspace_id == "personal"
    assert seen_prompt_args == [("u", None)]


def test_reset_sdk_loop_without_session_removes_all_user_loops_explicitly(monkeypatch):
    runner._loop_cache.clear()
    runner._loop_cache[runner._loop_cache_key("u", "ws", None, None, "default")] = object()
    runner._loop_cache[runner._loop_cache_key("u", "other", "openai:gpt-4.1", None, "chat-1")] = object()
    runner._loop_cache[runner._loop_cache_key("other", "ws", None, None, "default")] = object()

    removed = runner.reset_sdk_loop("u", workspace_id="ignored")

    assert removed == 2
    assert len(runner._loop_cache) == 1
    assert next(iter(runner._loop_cache)).startswith("other:")


def test_reset_user_sdk_loops_removes_all_workspaces_for_user(monkeypatch):
    runner._loop_cache.clear()
    runner._loop_cache[runner._loop_cache_key("u", "ws", None, None, "default")] = object()
    runner._loop_cache[runner._loop_cache_key("u", "other", "openai:gpt-4.1", None, "chat-1")] = object()
    runner._loop_cache[runner._loop_cache_key("other", "ws", None, None, "default")] = object()

    removed = runner.reset_user_sdk_loops("u", reason="test")

    assert removed == 2
    assert len(runner._loop_cache) == 1
    assert next(iter(runner._loop_cache)).startswith("other:")
