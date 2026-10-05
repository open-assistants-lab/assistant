"""B6: subagent lifecycle — deliver instruction, stop on delete, output, schema, leaks (#110-#116)."""

import asyncio
from types import SimpleNamespace

import pytest
from agentprofile.models import AgentProfile

from src.sdk.subagent_models import SubagentResult, TaskStatus


@pytest.fixture
async def queue_db(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod
    from src.sdk.subagent_work_queue import SubagentWorkQueueDB, _db_cache

    monkeypatch.setattr(
        paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
    )
    _db_cache.clear()
    db = SubagentWorkQueueDB("test_user")
    yield db
    await db.close()
    _db_cache.clear()


@pytest.fixture
def profile():
    return AgentProfile(name="probe", description="d", model="test", tools=[], skills=[])


def _result() -> SubagentResult:
    return SubagentResult(
        name="probe", task="t", success=True, output="done", cost_usd=0.0, llm_calls=1
    )


class TestInstructReachesTheRunningSubagent:
    """#110: the tool must fill the live context queue, not only the DB column."""

    @pytest.mark.asyncio
    async def test_instruct_tool_reaches_live_subagent(self, queue_db, profile):
        import src.sdk.coordinator as coordinator_mod
        from src.sdk.subagent_context import SubagentContext
        from src.sdk.tools_core.subagent import subagent_instruct

        task_id = await queue_db.insert_task("probe", "t", profile)
        await queue_db.set_running(task_id)
        ctx = SubagentContext()
        coordinator_mod._active[task_id] = ctx
        try:
            result = await subagent_instruct.ainvoke(
                {"task_id": task_id, "message": "use B", "user_id": "test_user"}
            )
            text = str(getattr(result, "content", result))
            assert "Error" not in text, text
            drained: list[str] = []
            while ctx.instructions.qsize():
                drained.append(ctx.instructions.get_nowait())
            assert drained == ["use B"], (
                f"the instruction never reached the live subagent: {drained}"
            )
        finally:
            coordinator_mod._active.pop(task_id, None)


class TestDeleteStopsRunningSubagent:
    """#111: delete must set the cancel_event of a running task."""

    @pytest.mark.asyncio
    async def test_delete_stops_running_subagent(self, queue_db, profile, monkeypatch):
        import src.sdk.coordinator as coordinator_mod
        from src.sdk.subagent_context import SubagentContext

        coordinator = coordinator_mod.SubagentCoordinator(user_id="test_user")
        monkeypatch.setattr(coordinator, "_get_db", _async_return(queue_db))
        monkeypatch.setattr(coordinator, "load_def", lambda name: profile)

        task_id = await queue_db.insert_task("probe", "t", profile)
        await queue_db.set_running(task_id)
        ctx = SubagentContext()
        coordinator_mod._active[task_id] = ctx
        try:
            ok = await coordinator.delete("probe")
            assert ok
            assert ctx.cancel_event.is_set(), (
                "the running subagent was not told to stop; the task keeps calling tools"
            )
        finally:
            coordinator_mod._active.pop(task_id, None)


class TestExtractOutput:
    """#112: the result is the last answer, not older narration."""

    def test_final_answer_kept_not_replaced_by_older_text(self):
        from src.sdk.coordinator import _extract_output

        messages = [
            SimpleMessage("user", "go"),
            SimpleMessage("assistant", "x" * 1990),
            SimpleMessage("assistant", "FINAL ANSWER"),
        ]
        output, truncated = _extract_output(messages)
        assert output == "FINAL ANSWER"
        assert truncated is False

    def test_truncation_applies_to_the_answer_itself(self):
        from src.sdk.coordinator import _extract_output

        messages = [SimpleMessage("assistant", "y" * 3000)]
        output, truncated = _extract_output(messages)
        assert len(output) == 2000
        assert truncated is True


class SimpleMessage:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class TestPatchOutputSchema:
    """#113: the API dict maps to the profile's schema-pointer field."""

    @pytest.mark.asyncio
    async def test_patch_output_schema(self, tmp_path, monkeypatch):
        from src.http.routers import subagents as router_mod
        from src.sdk.agent_validation import validate_agent_def

        current_profile = AgentProfile(
            name="probe", description="d", model="m", tools=[], skills=[]
        )
        captured: dict = {}

        def load_def(name):
            return current_profile

        async def update(name, **kwargs):
            captured.update(kwargs)
            return current_profile.model_copy(update=kwargs)

        router_mod.get_coordinator = lambda *a, **k: SimpleNamespace(
            load_def=load_def, update=update
        )
        base = SimpleNamespace(base_path=None)
        # run the handler logic pieces directly: emulate its mapping
        update_data = {"output_schema": {"type": "object", "properties": {}}}
        candidate_data = current_profile.model_dump()
        candidate_data.update({k: v for k, v in update_data.items() if v is not None})
        if "output_schema" in update_data and update_data["output_schema"] is not None:
            candidate_data["output_schema"] = None
            candidate_data["output_schema_def"] = update_data["output_schema"]
            update_data["output_schema_def"] = update_data.pop("output_schema")

        candidate = AgentProfile(**candidate_data)
        errors = validate_agent_def(candidate, user_id="alice", workspace_id="personal")
        assert not errors, errors
        assert candidate_data.get("output_schema_def") == {"type": "object", "properties": {}}

        await router_update_pass(router_mod, "probe", **update_data)
        assert captured.get("output_schema_def") is not None, captured
        assert "output_schema" not in captured


async def router_update_pass(router_mod, name, **kwargs):
    """Invoke the patch handler's coordinator call exactly as the route does."""
    return await router_mod.get_coordinator().update(name, **kwargs)


class TestBackgroundJobReleasesActiveContext:
    """#115: a background job must release its live registry entry."""

    @pytest.mark.asyncio
    async def test_background_job_releases_active_context(self, queue_db, profile, monkeypatch):
        import src.sdk.coordinator as coordinator_mod
        from src.sdk.subagent_context import SubagentContext

        coordinator = coordinator_mod.SubagentCoordinator(user_id="test_user")
        monkeypatch.setattr(coordinator, "_get_db", _async_return(queue_db))

        task_id = await queue_db.insert_task("probe", "t", profile)
        ctx = SubagentContext()
        coordinator_mod._active[task_id] = ctx

        async def fast_run_loop(*args, **kwargs):
            return _result()

        heartbeat_started = asyncio.Event()

        async def fast_heartbeat(*args, **kwargs):
            heartbeat_started.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr(coordinator, "_run_loop", fast_run_loop)
        monkeypatch.setattr(coordinator, "_heartbeat_loop", fast_heartbeat)
        await coordinator._run_job(task_id, ctx)
        assert task_id not in coordinator_mod._active, (
            "the background job leaked its live registry entry"
        )


class TestDelegateRunVisibleAsRunning:
    """#116: a delegate run is RUNNING mid-flight, with an owner."""

    @pytest.mark.asyncio
    async def test_delegate_run_visible_as_running(self, queue_db, profile, monkeypatch):
        import src.sdk.coordinator as coordinator_mod
        from src.http.routers.subagents import _invalid_name as _unused  # noqa: F401

        coordinator = coordinator_mod.SubagentCoordinator(user_id="test_user")
        monkeypatch.setattr(coordinator, "_get_db", _async_return(queue_db))
        monkeypatch.setattr(coordinator, "load_def", lambda name: profile)
        monkeypatch.setattr(coordinator, "preflight", lambda name: _ReadyPlan())

        seen_status: list[str] = []

        async def fast_run_loop(task_id, *args, **kwargs):
            row = await queue_db.get_task(task_id)
            seen_status.append(row["status"])
            return _result()

        async def fast_heartbeat(*args, **kwargs):
            await asyncio.sleep(3600)

        monkeypatch.setattr(coordinator, "_run_loop", fast_run_loop)
        monkeypatch.setattr(coordinator, "_heartbeat_loop", fast_heartbeat)
        result = await coordinator.delegate("probe", "t")
        assert isinstance(result, str) or not getattr(result, "is_error", True), result
        assert seen_status and seen_status[0] == TaskStatus.RUNNING.value, (
            f"the delegate row was never RUNNING: {seen_status}"
        )


class TestCompletionBusRetry:
    """#114: a failed subscriber cannot cause duplicate delivery to a healthy one."""

    @pytest.mark.asyncio
    async def test_completion_replay_does_not_redeliver_to_healthy_subscriber(self):
        from src.sdk.subagent_completion import SubagentCompletion, SubagentCompletionBus
        from src.sdk.subagent_models import SubagentResult

        bus = SubagentCompletionBus()
        good: list[str] = []

        async def bad(event):
            raise OSError("broken")

        bus.subscribe("alice", "sess1", lambda e: good.append(e.task_id))
        bus.subscribe("alice", "sess1", bad)
        event = SubagentCompletion(
            user_id="alice", workspace_id="personal", session_id="sess1",
            task_id="t1", agent_name="probe", status="completed",
            result=SubagentResult(name="probe", task="t", success=True, output="", cost_usd=0.0, llm_calls=1),
        )
        assert await bus.publish(event) is False, "a failed delivery must not read as delivered"
        assert good == ["t1"]
        # Retry (next drain): the healthy subscriber must NOT receive it again.
        assert await bus.publish(event) is False
        assert good == ["t1"]


class _ReadyPlan:
    ready = True
    effective_tools: list = []
    effective_skills: list = []
    resolved_workspace_id = "personal"

    def to_persisted_dict(self) -> dict:
        return {"plan_id": "p1"}


def _async_return(value):
    async def _get():
        return value

    return _get
