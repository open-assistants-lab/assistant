import asyncio
import inspect

import pytest


def test_new_runtime_tools_registered():
    from src.sdk.native_tools import get_native_tools

    names = {t.name for t in get_native_tools()}
    assert "subagent_start" in names
    assert "subagent_check" in names
    assert "subagent_tasks" in names
    assert {"subagent_start", "subagent_check", "subagent_tasks"}.issubset(names)


def test_all_subagent_tools_are_native_async_without_bridge():
    from src.sdk.tools_core import subagent as mod

    for name in [
        "subagent_create",
        "subagent_update",
        "subagent_start",
        "subagent_delegate",
        "subagent_list",
        "subagent_check",
        "subagent_tasks",
        "subagent_instruct",
        "subagent_cancel",
        "subagent_delete",
    ]:
        tool_def = getattr(mod, name)
        assert inspect.iscoroutinefunction(tool_def.function), name
        assert tool_def._coroutine is not None, name

    assert not hasattr(mod, "_run_async")
    assert not hasattr(mod, "_recreate_loop")
    assert not hasattr(mod, "_get_loop")


@pytest.mark.asyncio
async def test_subagent_start_returns_job_id(monkeypatch):
    from src.sdk.tools_core import subagent as mod

    class FakeCoordinator:
        def load_def(self, name):
            return object()

        async def start(self, agent_name, task, parent_id=None):
            return "job123"

    monkeypatch.setattr(
        mod, "get_coordinator", lambda user_id, workspace_id: FakeCoordinator(), raising=False
    )
    result = await mod.subagent_start.ainvoke(
        {
            "agent_name": "worker",
            "task": "do work",
            "user_id": "u",
            "workspace_id": "w",
        }
    )
    assert "job123" in result
    assert "subagent_check" in result


@pytest.mark.asyncio
async def test_subagent_check_returns_single_job_status(monkeypatch):
    from src.sdk.tools_core import subagent as mod

    class FakeDB:
        async def get_task(self, task_id):
            assert task_id == "job123"
            return {
                "id": "job123",
                "agent_name": "worker",
                "status": "completed",
                "progress": '{"steps_completed": 2, "phase": "done"}',
                "result": '{"output": "finished", "truncated": false}',
            }

    class FakeCoordinator:
        async def _get_db(self):
            return FakeDB()

    monkeypatch.setattr(
        mod, "get_coordinator", lambda user_id, workspace_id: FakeCoordinator(), raising=False
    )

    result = await mod.subagent_check.ainvoke(
        {"task_id": "job123", "user_id": "u", "workspace_id": "w"}
    )

    assert "job123" in result
    assert "completed" in result
    assert "finished" in result


@pytest.mark.asyncio
async def test_subagent_tasks_filters_by_status(monkeypatch):
    from src.sdk.subagent_models import TaskStatus
    from src.sdk.tools_core import subagent as mod

    seen = {}

    class FakeDB:
        async def check_progress(self, status=None):
            seen["status"] = status
            return [{"id": "job123", "agent_name": "worker", "status": "running"}]

    class FakeCoordinator:
        async def _get_db(self):
            return FakeDB()

    monkeypatch.setattr(
        mod, "get_coordinator", lambda user_id, workspace_id: FakeCoordinator(), raising=False
    )

    result = await mod.subagent_tasks.ainvoke(
        {"status": "running", "user_id": "u", "workspace_id": "w"}
    )

    assert seen["status"] == TaskStatus.RUNNING
    assert "job123" in result
    assert "worker" in result


@pytest.mark.asyncio
async def test_subagent_tasks_rejects_invalid_status(monkeypatch):
    from src.sdk.tools_core import subagent as mod

    result = await mod.subagent_tasks.ainvoke(
        {"status": "not-a-status", "user_id": "u", "workspace_id": "w"}
    )

    assert result.startswith("Error: Invalid status")
    assert "running" in result


@pytest.mark.asyncio
async def test_subagent_create_parses_new_json_fields_and_validates(monkeypatch):
    from src.sdk.tools_core import subagent as mod

    saved = {}

    class FakeCoordinator:
        def load_def(self, name):
            return None

        async def create(self, profile):
            saved["profile"] = profile
            return profile

    monkeypatch.setattr(
        mod, "get_coordinator", lambda user_id, workspace_id: FakeCoordinator(), raising=False
    )
    monkeypatch.setattr(mod, "validate_agent_def", lambda profile, **kwargs: [], raising=False)

    result = await mod.subagent_create.ainvoke(
        {
            "name": "worker",
            "user_id": "u",
            "workspace_id": "w",
            "description": "test worker",
            "model": "anthropic:claude-sonnet-4-20250514",
            "provider_options": '{"anthropic": {"thinking": {"type": "enabled"}}}',
            "output_schema": '{"type": "object"}',
            "handoff_instructions": "return concise output",
        }
    )

    profile = saved["profile"]
    assert "created successfully" in result
    assert profile.provider_options == {"anthropic": {"thinking": {"type": "enabled"}}}
    assert profile.output_schema_def == {"type": "object"}
    assert profile.handoff_instructions == "return concise output"


@pytest.mark.asyncio
async def test_subagent_create_rejects_non_object_provider_options(monkeypatch):
    from src.sdk.tools_core import subagent as mod

    result = await mod.subagent_create.ainvoke(
        {"name": "worker", "user_id": "u", "provider_options": '["bad"]'}
    )

    assert result == "Error: provider_options must be a JSON object."


@pytest.mark.asyncio
async def test_subagent_update_parses_new_fields_and_validates_before_save(monkeypatch):
    from agentprofile.models import AgentProfile

    from src.sdk.tools_core import subagent as mod

    saved = {}
    validated = {}
    existing = AgentProfile(name="worker", description="old", model="anthropic:claude-sonnet-4-20250514")

    class FakeCoordinator:
        def load_def(self, name):
            return existing

        async def update(self, name, **kwargs):
            saved["name"] = name
            saved["kwargs"] = kwargs
            return existing.model_copy(update=kwargs)

    def fake_validate(profile, **kwargs):
        validated["profile"] = profile
        validated["kwargs"] = kwargs
        return []

    monkeypatch.setattr(
        mod, "get_coordinator", lambda user_id, workspace_id: FakeCoordinator(), raising=False
    )
    monkeypatch.setattr(mod, "validate_agent_def", fake_validate, raising=False)

    result = await mod.subagent_update.ainvoke(
        {
            "name": "worker",
            "user_id": "u",
            "workspace_id": "w",
            "provider_options": '{"anthropic": {"thinking": {"type": "enabled"}}}',
            "output_schema": '{"type": "object"}',
            "handoff_instructions": "return concise output",
        }
    )

    assert "updated" in result
    assert saved["name"] == "worker"
    assert saved["kwargs"]["provider_options"] == {
        "anthropic": {"thinking": {"type": "enabled"}}
    }
    assert saved["kwargs"]["output_schema_def"] == {"type": "object"}
    assert saved["kwargs"]["handoff_instructions"] == "return concise output"
    assert validated["profile"].provider_options == {
        "anthropic": {"thinking": {"type": "enabled"}}
    }
    assert validated["profile"].output_schema_def == {"type": "object"}
    assert validated["profile"].handoff_instructions == "return concise output"
    assert validated["kwargs"] == {"user_id": "u", "workspace_id": "w"}


@pytest.mark.asyncio
async def test_subagent_update_rejects_invalid_provider_options_json(monkeypatch):
    from agentprofile.models import AgentProfile

    from src.sdk.tools_core import subagent as mod

    class FakeCoordinator:
        def load_def(self, name):
            return AgentProfile(name="worker", description="a worker", model="anthropic:claude-sonnet-4-20250514")

        async def update(self, name, **kwargs):
            raise AssertionError("update should not be called")

    monkeypatch.setattr(
        mod, "get_coordinator", lambda user_id, workspace_id: FakeCoordinator(), raising=False
    )

    result = await mod.subagent_update.ainvoke(
        {"name": "worker", "user_id": "u", "provider_options": "{"}
    )

    assert result.startswith("Error: Invalid provider_options JSON")


@pytest.mark.asyncio
async def test_subagent_update_rejects_invalid_output_schema_json(monkeypatch):
    from agentprofile.models import AgentProfile

    from src.sdk.tools_core import subagent as mod

    class FakeCoordinator:
        def load_def(self, name):
            return AgentProfile(name="worker", description="a worker", model="anthropic:claude-sonnet-4-20250514")

        async def update(self, name, **kwargs):
            raise AssertionError("update should not be called")

    monkeypatch.setattr(
        mod, "get_coordinator", lambda user_id, workspace_id: FakeCoordinator(), raising=False
    )

    result = await mod.subagent_update.ainvoke(
        {"name": "worker", "user_id": "u", "output_schema": "{"}
    )

    assert result.startswith("Error: Invalid output_schema JSON")


@pytest.mark.asyncio
async def test_subagent_update_rejects_validation_errors_before_save(monkeypatch):
    from agentprofile.models import AgentProfile

    from src.sdk.tools_core import subagent as mod

    validated = {}

    class FakeCoordinator:
        def load_def(self, name):
            return AgentProfile(name="worker", description="a worker", model="anthropic:claude-sonnet-4-20250514")

        async def update(self, name, **kwargs):
            raise AssertionError("update should not be called")

    def fake_validate(profile, **kwargs):
        validated["tools"] = profile.tools
        return ["Unknown tool: not_a_tool"]

    monkeypatch.setattr(
        mod, "get_coordinator", lambda user_id, workspace_id: FakeCoordinator(), raising=False
    )
    monkeypatch.setattr(mod, "validate_agent_def", fake_validate, raising=False)

    result = await mod.subagent_update.ainvoke(
        {"name": "worker", "user_id": "u", "tools": ["not_a_tool"]}
    )

    assert result == "Error: Unknown tool: not_a_tool"
    assert validated["tools"] == ["not_a_tool"]


def test_subagent_delegate_registered():
    from src.sdk.native_tools import get_native_tools

    names = {t.name for t in get_native_tools()}
    assert "subagent_delegate" in names


@pytest.mark.asyncio
async def test_subagent_delegate_returns_output(monkeypatch):
    from src.sdk.tools_core import subagent as mod

    class FakeCoordinator:
        def load_def(self, name):
            return object()

        async def delegate(self, agent_name, task, parent_id=None, timeout_seconds=None):
            return "finished work"

    monkeypatch.setattr(
        mod, "get_coordinator", lambda user_id, workspace_id: FakeCoordinator(), raising=False
    )
    result = await mod.subagent_delegate.ainvoke(
        {
            "agent_name": "worker",
            "task": "do work",
            "user_id": "u",
            "workspace_id": "w",
        }
    )
    assert result == "finished work"


@pytest.mark.asyncio
async def test_subagent_delegate_error_for_nonexistent_agent(monkeypatch):
    from src.sdk.tools_core import subagent as mod

    class FakeCoordinator:
        def load_def(self, name):
            return None

    monkeypatch.setattr(
        mod, "get_coordinator", lambda user_id, workspace_id: FakeCoordinator(), raising=False
    )
    result = await mod.subagent_delegate.ainvoke(
        {
            "agent_name": "nobody",
            "task": "do work",
            "user_id": "u",
        }
    )
    assert "not found" in result


@pytest.mark.asyncio
async def test_subagent_delegate_timeout(monkeypatch):
    from src.sdk.tools_core import subagent as mod

    class FakeCoordinator:
        def load_def(self, name):
            return object()

        async def delegate(self, agent_name, task, parent_id=None, timeout_seconds=None):
            raise TimeoutError("timed out")

    monkeypatch.setattr(
        mod, "get_coordinator", lambda user_id, workspace_id: FakeCoordinator(), raising=False
    )
    result = await mod.subagent_delegate.ainvoke(
        {
            "agent_name": "slow",
            "task": "do work",
            "user_id": "u",
            "timeout_seconds": 1,
        }
    )
    assert "Timeout" in result


def test_subagent_delegate_requires_hitl_not_parallel_safe():
    """Delegation can write or hit the network, so it must not be marked read-only/idempotent."""
    from src.sdk.tools_core import subagent as mod

    ann = mod.subagent_delegate.annotations
    assert ann.read_only is False
    assert ann.idempotent is False
    assert ann.destructive is True



@pytest.mark.asyncio
async def test_concurrent_delegate_and_start_share_current_event_loop(monkeypatch):
    from src.sdk.tools_core import subagent as mod

    running_loop = asyncio.get_running_loop()
    seen: list[asyncio.AbstractEventLoop] = []

    class FakeCoordinator:
        def load_def(self, name):
            return object()

        async def start(self, agent_name, task, parent_id=None):
            seen.append(asyncio.get_running_loop())
            await asyncio.sleep(0)
            return "job123"

        async def delegate(self, agent_name, task, parent_id=None, timeout_seconds=None):
            seen.append(asyncio.get_running_loop())
            await asyncio.sleep(0)
            return "finished work"

    monkeypatch.setattr(
        mod, "get_coordinator", lambda user_id, workspace_id: FakeCoordinator(), raising=False
    )

    start_result, delegate_result = await asyncio.gather(
        mod.subagent_start.ainvoke(
            {"agent_name": "worker", "task": "background", "user_id": "u", "workspace_id": "w"}
        ),
        mod.subagent_delegate.ainvoke(
            {"agent_name": "worker", "task": "inline", "user_id": "u", "workspace_id": "w"}
        ),
    )

    assert "job123" in start_result
    assert delegate_result == "finished work"
    assert seen == [running_loop, running_loop]
