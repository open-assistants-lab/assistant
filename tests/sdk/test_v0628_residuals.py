"""Residuals found after v0.6.28: #62-#67 (and the reopened #51/#53/#58/#59)."""

import json

import pytest

from src.sdk.loop import AgentLoop
from src.sdk.messages import Message, ToolCall
from src.sdk.state import AgentState
from src.sdk.tools import ToolAnnotations, ToolDefinition, tool


@tool
async def reader(path: str) -> str:
    """Read a file."""
    return f"contents of {path}"


reader.annotations = ToolAnnotations(title="Read", read_only=True, idempotent=True)


@tool
async def writer(path: str = "", body: str = "") -> str:
    """Change the world."""
    return f"wrote {path or '(default)'}"


class TestDefinitionDriftIsRefused:
    """#62: an approved name must run the body that was approved."""

    @pytest.fixture
    async def svc(self, tmp_path, monkeypatch):
        import src.sdk.governance as gov
        import src.storage.paths as paths_mod
        from src.sdk.governance import GovernanceService

        monkeypatch.setattr(
            paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
        )
        monkeypatch.setattr(gov, "_services", {})
        service = GovernanceService()
        monkeypatch.setattr(service, "resolve_permission", lambda *a: "ask")
        yield service
        for kernel in service._execution_kernels.values():
            await kernel._store.close()

    @staticmethod
    def _definition(marker: str) -> ToolDefinition:
        async def body() -> str:
            return marker

        return ToolDefinition(
            name="probe",
            description="probe",
            parameters={"type": "object", "properties": {}},
            function=body,
        )

    @pytest.mark.asyncio
    async def test_a_replacement_body_is_refused(self, svc, monkeypatch):
        import src.sdk.runner as runner
        from src.sdk.governance import definition_fingerprint

        original, replacement = self._definition("ORIGINAL"), self._definition("REPLACEMENT")
        current = {"td": original}
        monkeypatch.setattr(runner, "get_active_tool_definition", lambda *a, **k: current["td"])

        pid = svc.create_pending(
            "erin", "probe", {}, permission="ask",
            definition_hash=definition_fingerprint(original),
        )
        svc.approve("erin", pid)
        current["td"] = replacement

        result = await svc.execute_approved("erin", pid)
        assert result["is_error"], "a drifted definition executed anyway"
        assert "definition" in str(result["structured_content"]).lower()

    @pytest.mark.asyncio
    async def test_an_unchanged_definition_still_executes(self, svc, monkeypatch):
        import src.sdk.runner as runner
        from src.sdk.governance import definition_fingerprint

        definition = self._definition("ORIGINAL")
        monkeypatch.setattr(runner, "get_active_tool_definition", lambda *a, **k: definition)
        pid = svc.create_pending(
            "erin", "probe", {}, permission="ask",
            definition_hash=definition_fingerprint(definition),
        )
        svc.approve("erin", pid)
        result = await svc.execute_approved("erin", pid)
        assert result["is_error"] is False
        assert result["content"] == "ORIGINAL"

    def test_custom_lookup_receives_the_workspace(self, monkeypatch):
        """A 'project' request must not resolve the personal-workspace body."""
        import src.sdk.runner as runner
        import src.sdk.tools_custom as custom

        seen: list = []

        def capture(user_id, workspace_id=None):
            seen.append(workspace_id)
            return []

        monkeypatch.setattr(custom, "get_custom_tools", capture)
        monkeypatch.setattr(runner, "filter_denied_native_tools", lambda tools, settings=None: [])
        runner.get_active_tool_definition("erin", "probe", workspace_id="project")
        assert seen == ["project"]


class TestRecoveryIsRaceSafe:
    """#63: a heartbeat failure must not strand cleanup; recovery re-checks."""

    @pytest.mark.asyncio
    async def test_heartbeat_failure_does_not_strand_the_active_entry(
        self, tmp_path, monkeypatch
    ):
        import src.sdk.coordinator as coordinator_mod
        import src.storage.paths as paths_mod
        from src.sdk.subagent_work_queue import SubagentWorkQueueDB

        monkeypatch.setattr(
            paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
        )
        monkeypatch.setattr(subagent_work_queue_cache := __import__(
            "src.sdk.subagent_work_queue", fromlist=["_db_cache"]
        ), "_db_cache", {})
        queue = SubagentWorkQueueDB("test_user")
        try:
            coordinator = coordinator_mod.SubagentCoordinator(user_id="test_user")

            async def get_db():
                return queue

            async def broken_heartbeat(task_id, worker_id, db):
                raise OSError("disk gone")

            monkeypatch.setattr(coordinator, "_get_db", get_db)
            monkeypatch.setattr(coordinator, "preflight", lambda name: _ReadyPlan())
            monkeypatch.setattr(coordinator, "load_def", lambda name: _agent_profile())
            monkeypatch.setattr(coordinator_mod, "_subagent_enabled", lambda *a: True)
            monkeypatch.setattr(coordinator, "_heartbeat_loop", broken_heartbeat)
            monkeypatch.setattr(
                coordinator, "_run_loop", _async_return(_Result())
            )
            task_id = await coordinator.invoke("probe", "t")
            assert task_id not in coordinator_mod._active, (
                "a failing heartbeat stranded the task in the live registry"
            )
        finally:
            await queue.close()
            subagent_work_queue_cache._db_cache.clear()

    @pytest.mark.asyncio
    async def test_a_refreshed_heartbeat_is_not_failed_by_the_sweep(self, tmp_path, monkeypatch):
        import src.sdk.subagent_work_queue as wq_mod
        import src.storage.paths as paths_mod
        from src.sdk.subagent_models import TaskStatus

        monkeypatch.setattr(
            paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
        )
        wq_mod._db_cache.clear()
        queue = wq_mod.SubagentWorkQueueDB("test_user")
        try:
            profile = _agent_profile()
            task_id = await queue.insert_task("probe", "t", profile)
            await queue.set_running(task_id)
            handle = await queue._get_db()
            await handle.execute(
                "UPDATE work_queue SET heartbeat_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                (task_id,),
            )
            await handle.commit()

            real_execute = handle.execute

            async def refreshing_execute(sql, *args, **kwargs):
                # The sweep SELECTs first; a live worker refreshes in between.
                flat = " ".join(sql.split()).upper()
                if flat.startswith("UPDATE WORK_QUEUE SET STATUS = ?") and "COMPLETED_AT" in flat:
                    from src.sdk.subagent_work_queue import _now

                    await real_execute(
                        "UPDATE work_queue SET heartbeat_at = ? WHERE id = ?",
                        (_now(), task_id),
                    )
                return await real_execute(sql, *args, **kwargs)

            monkeypatch.setattr(handle, "execute", refreshing_execute)
            recovered = await queue.mark_stale_running_failed(max_age_seconds=300)
            assert recovered == 0
            assert (await queue.get_task(task_id))["status"] == TaskStatus.RUNNING.value
        finally:
            await queue.close()
            wq_mod._db_cache.clear()


class TestTransitionMap:
    """#64: set_status may not walk a transition backwards."""

    @pytest.mark.asyncio
    async def test_cancelling_cannot_go_back_to_running(self, tmp_path, monkeypatch):
        import src.sdk.subagent_work_queue as wq_mod
        import src.storage.paths as paths_mod
        from src.sdk.subagent_models import TaskStatus

        monkeypatch.setattr(
            paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
        )
        wq_mod._db_cache.clear()
        queue = wq_mod.SubagentWorkQueueDB("test_user")
        try:
            task_id = await queue.insert_task("probe", "t", _agent_profile())
            await queue.set_running(task_id)
            await queue.request_cancel(task_id)
            assert await queue.set_status(task_id, TaskStatus.RUNNING) is False
            assert (await queue.get_task(task_id))["status"] == TaskStatus.CANCELLING.value
            assert await queue.set_status(task_id, TaskStatus.CANCELLING) is False
        finally:
            await queue.close()
            wq_mod._db_cache.clear()

    @pytest.mark.asyncio
    async def test_the_permitted_transitions_still_work(self, tmp_path, monkeypatch):
        import src.sdk.subagent_work_queue as wq_mod
        import src.storage.paths as paths_mod
        from src.sdk.subagent_models import TaskStatus

        monkeypatch.setattr(
            paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
        )
        wq_mod._db_cache.clear()
        queue = wq_mod.SubagentWorkQueueDB("test_user")
        try:
            task_id = await queue.insert_task("probe", "t", _agent_profile())
            assert await queue.set_status(task_id, TaskStatus.RUNNING) is True
            assert await queue.set_status(task_id, TaskStatus.CANCELLING) is True
        finally:
            await queue.close()
            wq_mod._db_cache.clear()


class TestNullPathOwnership:
    """#65: the default path is checked too."""

    @pytest.fixture
    def tenant(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from src.sdk.tools_core import filesystem as fs_mod
        from src.storage.paths import DataPaths

        root = tmp_path / "store"
        settings = SimpleNamespace(filesystem=SimpleNamespace(allowed_roots=[], workspace_root=None))
        import src.config as cfg

        monkeypatch.setattr(cfg, "get_settings", lambda: settings)
        monkeypatch.setattr(
            fs_mod,
            "get_paths",
            lambda user, workspace_id="personal": DataPaths(
                user_id=user, data_root=root, data_path=tmp_path / "cfg",
                workspace_id=workspace_id,
            ),
        )
        return root

    @pytest.mark.asyncio
    async def test_a_symlinked_workspace_does_not_expose_a_sibling(self, tenant):
        from src.sdk.tools_core import filesystem as fs_mod

        alice = fs_mod.get_paths("alice")
        alice_ws = alice.workspace_files_dir()
        bob_ws = fs_mod.get_paths("bob").workspace_files_dir()
        (bob_ws / "bob_secret.txt").write_text("secret")
        alice_ws.rmdir()
        alice_ws.symlink_to(bob_ws)

        listing = await fs_mod.files_list.ainvoke({"path": None, "user_id": "alice"})
        assert "bob_secret.txt" not in str(getattr(listing, "content", listing))

    @pytest.mark.asyncio
    async def test_a_non_string_path_is_refused(self, tenant):
        from src.sdk.tools_core import filesystem as fs_mod

        listing = await fs_mod.files_list.ainvoke({"path": [".."], "user_id": "alice"})
        rendered = str(getattr(listing, "content", listing))
        assert getattr(listing, "is_error", False) or "string" in rendered.lower(), (
            "a non-string path was not refused"
        )


class TestReadCacheInvalidation:
    """#66: aliases, argument-less writes and same-batch ordering."""

    def _state_with_cached_read(self, path: str = "x") -> AgentState:
        loop = AgentLoop(provider=object(), tools=[reader])
        state = AgentState(messages=[])
        loop._record_executed_tools([ToolCall(id="r1", name="reader", arguments={"path": path})], state)
        return state

    @pytest.mark.asyncio
    async def test_a_write_without_arguments_invalidates_cached_reads(self):
        loop = AgentLoop(provider=object(), tools=[reader, writer])
        state = self._state_with_cached_read()
        loop._record_executed_tools(
            [ToolCall(id="w1", name="writer", arguments={"body": "everything"})], state
        )
        fresh, dupes = loop._split_duplicate_tool_calls(
            [ToolCall(id="r2", name="reader", arguments={"path": "x"})], state
        )
        assert [c.id for c in fresh] == ["r2"], "a blanket write did not invalidate the read"

    @pytest.mark.asyncio
    async def test_an_equivalent_path_invalidates_the_cached_read(self):
        loop = AgentLoop(provider=object(), tools=[reader, writer])
        state = self._state_with_cached_read("x")
        loop._record_executed_tools(
            [ToolCall(id="w1", name="writer", arguments={"path": "./x", "body": "new"})], state
        )
        fresh, _ = loop._split_duplicate_tool_calls(
            [ToolCall(id="r2", name="reader", arguments={"path": "x"})], state
        )
        assert [c.id for c in fresh] == ["r2"]

    @pytest.mark.asyncio
    async def test_a_read_after_a_write_in_the_same_response_is_not_synthesized(self):
        """One response containing write('x') and read('x') must execute the read."""
        from tests.sdk.test_sdk_loop import MockProvider

        provider = MockProvider(responses=[
            Message.assistant(tool_calls=[ToolCall(id="r1", name="reader", arguments={"path": "x"})]),
            Message.assistant(tool_calls=[
                ToolCall(id="w1", name="writer", arguments={"path": "x", "body": "new"}),
                ToolCall(id="r2", name="reader", arguments={"path": "x"}),
            ]),
            Message.assistant(content="done"),
        ])
        loop = AgentLoop(provider=provider, tools=[reader, writer])
        result = await loop.run([Message.user("write then verify")])
        reads = [
            m for m in result
            if m.role == "tool" and m.name == "reader"
            and not str(m.content).startswith("Duplicate call skipped")
        ]
        assert len(reads) == 2, "the read after the write in the same response was synthesized"

    @pytest.mark.asyncio
    async def test_seeded_retry_reads_are_invalidation_indexed(self):
        """The retry path must register values, or the first write cannot clear it."""
        loop = AgentLoop(provider=object(), tools=[reader, writer])
        state = AgentState(messages=[
            Message.user("do it"),
            Message.assistant(tool_calls=[ToolCall(id="c1", name="reader", arguments={"path": "x"})]),
            Message.tool_result(tool_call_id="c1", content="contents of x", name="reader"),
        ])
        loop._seed_executed_tool_calls(state)
        loop._record_executed_tools(
            [ToolCall(id="w1", name="writer", arguments={"path": "x", "body": "new"})], state
        )
        fresh, _ = loop._split_duplicate_tool_calls(
            [ToolCall(id="c2", name="reader", arguments={"path": "x"})], state
        )
        assert [c.id for c in fresh] == ["c2"]


class TestBlockedOutputScrubsReasoning:
    """#67: reasoning survives an output block."""

    @pytest.mark.asyncio
    async def test_reasoning_is_scrubbed_with_the_content(self):
        from src.sdk.guardrails import GuardrailResult
        from tests.sdk.test_sdk_loop import MockProvider

        class _Trip:
            name = "secret_filter"

            async def check(self, output, state):
                return GuardrailResult(tripwire_triggered=True, message="sensitive output")

        provider = MockProvider(responses=[
            Message.assistant(content="SECRET", reasoning="SECRET REASONING")
        ])
        loop = AgentLoop(provider=provider, output_guardrails=[_Trip()])
        result = await loop.run([Message.user("go")])
        assert not any("SECRET" in str(m.content or "") for m in result)
        assert not any(
            "SECRET" in str(getattr(m, "reasoning", "") or "") for m in result
        ), "reasoning survived the output block"


def _agent_profile():
    """A real AgentProfile: validate_agent_def inspects its fields."""
    from agentprofile.models import AgentProfile

    return AgentProfile(name="probe", description="probe", model="test", tools=[], skills=[])


def _profile_stub():
    """Minimal object with just the JSON the queue persists."""

    class _Stub:
        def model_dump_json(self) -> str:
            return json.dumps({"name": "probe"})

    return _Stub()


class _Result:
    terminal_reason = ""
    error = None
    error_code = None
    success = True

    def model_dump_json(self) -> str:
        return "{}"


class _ReadyPlan:
    ready = True
    effective_tools: list = []
    effective_skills: list = []
    resolved_workspace_id = "personal"

    def to_persisted_dict(self) -> dict:
        return {"plan_id": "p1"}


def _async_return(value):
    async def _call():
        return value

    return _call
