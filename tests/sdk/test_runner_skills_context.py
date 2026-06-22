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


def test_get_skills_context_uses_workspace_registry(monkeypatch):
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

    context = runner._get_skills_context("u")

    assert calls == [{"user_id": "u"}]
    assert "project-helper" in context
    assert "Project-specific instructions." in context
    assert "FULL CONTENT SHOULD NOT BE INCLUDED" not in context
    assert "scope" not in context.lower()
    assert "(workspace)" not in context.lower()


def test_get_system_prompt_calls_skills_context(monkeypatch):
    calls = []

    def fake_get_skill_registry(**kwargs):
        calls.append(kwargs)
        return FakeRegistry([
            {"name": "ws-skill", "description": "Skill from ws1.", "metadata": {}}
        ])

    monkeypatch.setattr(skills_registry, "get_skill_registry", fake_get_skill_registry)

    prompt = runner._get_system_prompt("u")

    assert calls == [{"user_id": "u"}]
    assert "ws-skill" in prompt


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

    class FakeLoop:
        async def run_stream(self, messages):
            recorded.extend(messages)
            if False:
                yield None

    async def fake_get_sdk_loop(*args, **kwargs):
        return FakeLoop()

    monkeypatch.setattr(runner, "get_sdk_loop", fake_get_sdk_loop)
    chunks = runner.run_sdk_agent_stream(
        user_id="u",
        messages=[Message.system("base")],
    )
    async for _ in chunks:
        pass

    assert recorded[0].content == "base"


async def test_get_sdk_loop_reuses_provider_key_loop_for_runtime_state(monkeypatch):
    runner._loop_cache.clear()
    created = []

    async def fake_create_sdk_loop(*args, **kwargs):
        loop = object()
        created.append(loop)
        return loop

    monkeypatch.setattr(runner, "create_sdk_loop", fake_create_sdk_loop)

    keys = {"openai": "test-key"}
    first = await runner.get_sdk_loop("u", model="openai:gpt-4.1", provider_keys=keys)
    second = await runner.get_sdk_loop("u", model="openai:gpt-4.1", provider_keys=keys)

    assert second is first
    assert created == [first]


def test_reset_sdk_loop_removes_user_loops(monkeypatch):
    runner._loop_cache.clear()
    runner._loop_cache["u:default"] = object()
    runner._loop_cache["u:openai:gpt-4.1"] = object()
    runner._loop_cache["other:default"] = object()

    runner.reset_sdk_loop("u")

    assert set(runner._loop_cache) == {"other:default"}
