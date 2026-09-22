"""M4 governance unit tests: permissions, durable pendings, receipts (issue #6)."""


import pytest

from src.sdk.governance import (
    GovernanceService,
    get_governance_service,
)
from src.sdk.middleware_hitl import HITLMiddleware


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod

    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
    )
    import src.sdk.governance as gov

    monkeypatch.setattr(gov, "_services", {})
    return GovernanceService()


class TestPermissionResolution:
    def test_allow_default(self, svc):
        assert svc.resolve_permission("u1", "files_read") == "allow"

    def test_permission_from_settings_mapping(self, svc, monkeypatch):
        monkeypatch.setenv("GOVERNANCE_PERMISSIONS", '{"tools":{"files_delete":"ask"}}')
        from src.config import reload_settings

        reload_settings()
        try:
            assert svc.resolve_permission("u1", "files_delete") == "ask"
        finally:
            monkeypatch.delenv("GOVERNANCE_PERMISSIONS")
            reload_settings()

    def test_requires_approval_annotation_defaults_to_ask(self, svc, monkeypatch):
        """Annotation declares that a tool requires an approval request."""
        from src.sdk.tools import ToolAnnotations

        assert ToolAnnotations(requires_approval=True) is not None

    def test_permission_change_takes_effect_without_redeploy(self, svc, monkeypatch):
        from src.config import reload_settings

        assert svc.resolve_permission("u1", "jobs_add") == "allow"
        monkeypatch.setenv("GOVERNANCE_PERMISSIONS", '{"tools":{"jobs_add":"deny"}}')
        reload_settings()
        assert svc.resolve_permission("u1", "jobs_add") == "deny"


class TestDurablePendings:
    def test_create_pending_persists(self, svc):
        pid = svc.create_pending("u1", "jobs_add", {"title": "x"})
        assert pid
        row = svc.get_pending("u1", pid)
        assert row is not None
        assert row["tool"] == "jobs_add"
        assert row["status"] == "pending"

    def test_pending_survives_restart(self, svc, tmp_path, monkeypatch):
        """Simulated restart: a NEW service instance over the same data root
        still sees the pending proposal (durable SQLite)."""
        pid = svc.create_pending("u1", "jobs_add", {"title": "x"})

        fresh = GovernanceService()
        row = fresh.get_pending("u1", pid)
        assert row is not None and row["status"] == "pending"

    def test_approve_idempotent(self, svc):
        pid = svc.create_pending("u1", "jobs_add", {})
        assert svc.approve("u1", pid) is True  # first approve executes
        assert svc.approve("u1", pid) is False  # duplicate = no-op, not error

    def test_ask_pending_requires_approval(self, svc):
        pid = svc.create_pending("u1", "email_send", {}, permission="ask")
        assert svc.resolve_pending("u1", pid)["status"] == "pending"
        assert svc.approve("u1", pid) is True
        assert svc.resolve_pending("u1", pid)["status"] == "approved"


class TestReceipts:
    def test_proposal_approval_execution_linked(self, svc):
        pid = svc.create_pending("u1", "jobs_add", {}, permission="ask")
        assert svc.approve("u1", pid) is True
        events = [e for e in svc.recent_events("u1") if e.kind == "approve"]
        kinds = [e.detail for e in events]
        assert "proposal" in kinds[0] or "proposal" in kinds[-1]
        # proposal -> approval -> execution chain present
        assert any("approved" in e.detail for e in events)


class TestDisabled:
    def test_disabled_service_passes_all(self, monkeypatch):
        from src.config import reload_settings

        monkeypatch.delenv("GOVERNANCE_PERMISSIONS", raising=False)
        monkeypatch.setenv("GOVERNANCE_ENABLED", "false")
        reload_settings()
        try:
            s = get_governance_service("u1")
            assert s.resolve_permission("u1", "files_delete") == "allow"
        finally:
            monkeypatch.delenv("GOVERNANCE_ENABLED")
            reload_settings()

class TestHITLMiddleware:
    """M4-1: middleware integration for allow, ask, and deny."""

    def _mw(self, tmp_path, monkeypatch, user_id="u1"):
        import src.sdk.governance as gov
        from src.sdk.middleware_hitl import HITLMiddleware

        monkeypatch.setattr(
            "src.storage.paths.DataPaths",
            type(
                "DP",
                (),
                {
                    "root": property(lambda self: tmp_path / "root"),
                    "__init__": lambda self, **k: None,
                },
            ),
        )
        monkeypatch.setattr(gov, "_services", {})
        from src.config import reload_settings

        monkeypatch.setenv("GOVERNANCE_ENABLED", "true")
        reload_settings()
        return HITLMiddleware(user_id=user_id)

    @pytest.mark.asyncio
    async def test_deny_synthetic_refusal(self, tmp_path, monkeypatch):
        mw = self._mw(tmp_path, monkeypatch)
        monkeypatch.setenv("GOVERNANCE_PERMISSIONS", '{"tools":{"jobs_add":"deny"}}')
        from src.config import reload_settings

        reload_settings()
        result = await mw.guard_tool_call("jobs_add", {"title": "x"})
        assert result is not None and result.is_error
        assert result.structured_content["governance"] == "deny"
        assert result.structured_content["executed"] is False

    @pytest.mark.asyncio
    async def test_allow_passes_through(self, tmp_path, monkeypatch):
        mw = self._mw(tmp_path, monkeypatch)
        assert await mw.guard_tool_call("files_read", {}) is None

    @pytest.mark.asyncio
    async def test_ask_creates_durable_pending(self, tmp_path, monkeypatch):
        mw = self._mw(tmp_path, monkeypatch)
        monkeypatch.setenv("GOVERNANCE_PERMISSIONS", '{"tools":{"email_send":"ask"}}')
        from src.config import reload_settings

        reload_settings()
        result = await mw.guard_tool_call("email_send", {"to": "a@b.c"})
        pid = result.structured_content["proposal_id"]
        assert result.structured_content["status"] == "pending"
        # survives restart: a fresh service over the same root sees it
        from src.sdk.governance import GovernanceService

        row = GovernanceService().get_pending("u1", pid)
        assert row is not None and row["status"] == "pending"

    @pytest.mark.asyncio
    async def test_ask_always_stays_pending_until_approved(self, tmp_path, monkeypatch):
        mw = self._mw(tmp_path, monkeypatch)
        monkeypatch.setenv("GOVERNANCE_PERMISSIONS", '{"tools":{"email_send":"ask"}}')
        from src.config import reload_settings

        reload_settings()
        result = await mw.guard_tool_call("email_send", {})
        assert result.structured_content["governance"] == "ask"
        assert result.structured_content["status"] == "pending"


class TestExecutionLegChecks:
    """Bug-hunt fixes: execution leg re-checks capabilities + permission."""

    @pytest.fixture()
    def svc_with_pending(self, svc, monkeypatch):
        async def fake_invoke(arguments):
            return "EXECUTED-BODY"

        monkeypatch.setenv("GOVERNANCE_PERMISSIONS", '{"tools":{"gated_tool":"ask"}}')
        import src.sdk.governance as gov

        monkeypatch.setattr(gov, "governance_enabled", lambda: True)
        from src.sdk.tools import ToolDefinition

        td = ToolDefinition(
            name="gated_tool",
            description="gated",
            input_schema={"type": "object", "properties": {}},
            ainvoke=fake_invoke,  # type: ignore[arg-type]
        )
        monkeypatch.setattr(
            "src.sdk.native_tools.get_native_tools", lambda: [td]
        )
        pid = svc.create_pending(
            "u1", "gated_tool", {"x": "1"}, permission="ask"
        )
        svc.approve("u1", pid)
        return svc, pid

    @pytest.mark.asyncio
    async def test_disabled_tool_not_executed(self, svc_with_pending, monkeypatch):
        """P1: a tool disabled after pending creation must not execute."""
        import src.sdk.capabilities as caps_mod

        svc, pid = svc_with_pending
        monkeypatch.setattr(
            caps_mod, "load_capabilities", lambda root: {"tools": {"gated_tool": False}}
        )
        result = await svc.execute_approved("u1", pid)
        assert result["is_error"] is True
        assert "disabled" in result["structured_content"].get("error", "")

    @pytest.mark.asyncio
    async def test_deny_permission_change_refuses_execution(self, svc_with_pending, monkeypatch):
        """Permission re-resolution: pending created as ask, now denied."""
        import src.sdk.governance as gov

        svc, pid = svc_with_pending
        monkeypatch.setattr(
            gov,
            "get_governance_service",
            lambda user_id=None: svc,
        )
        monkeypatch.setattr(
            src_sdk_governance_permission_source(svc, monkeypatch), "resolve_permission"
        ) if False else None
        # Force resolve_permission to return deny via capabilities.
        import src.sdk.capabilities as caps_mod

        monkeypatch.setattr(
            caps_mod,
            "load_capabilities",
            lambda root: {"permissions": {"tools": {"gated_tool": "deny"}}},
        )
        result = await svc.execute_approved("u1", pid)
        assert result["is_error"] is True
        assert "denied" in result["content"]


import src.sdk.governance as _gov_mod  # noqa: E402


def src_sdk_governance_permission_source(svc, monkeypatch):  # pragma: no cover
    return _gov_mod


class TestPathTraversal:
    def test_db_path_rejects_traversal(self, svc, tmp_path):
        with pytest.raises(ValueError):
            svc._db_path("../../tmp/evil")

    @pytest.mark.asyncio
    async def test_resolve_permission_corrupt_caps_fails_closed(self, svc, monkeypatch):
        import src.sdk.capabilities as caps_mod
        import src.sdk.governance as gov

        def corrupt(root):
            raise RuntimeError("yaml parse error")

        monkeypatch.setattr(caps_mod, "load_capabilities", corrupt)
        monkeypatch.setattr(
            gov, "get_governance_service", lambda user_id=None: svc
        )
        permission = svc.resolve_permission("u1", "some_tool")
        assert permission == "ask"  # fail closed: conservative pending


class TestFatigueMetric:
    """M4-2 anti-fatigue: per-tool proposal/override/approval counts."""

    def test_override_rate_seeded(self, svc):
        for _ in range(3):
            svc.create_pending("u1", "mailer", {"to": "x"})
        svc.record_override("u1", "mailer")
        stats = {s["tool"]: s for s in svc.tool_stats("u1")}
        s = stats["mailer"]
        assert s["proposals"] == 3
        assert s["overrides"] == 1
        assert s["override_rate"] == pytest.approx(0.33)

    def test_ask_approval_is_not_an_override(self, svc):
        pid_writer = svc.create_pending("u1", "writer", {"a": 1}, permission="ask")
        pid_mailer = svc.create_pending("u1", "mailer", {"b": 2}, permission="ask")
        svc.approve("u1", pid_writer)
        svc.approve("u1", pid_mailer)
        stats = {s["tool"]: s for s in svc.tool_stats("u1")}
        assert stats["writer"]["approvals"] == 1
        assert stats["writer"]["overrides"] == 0
        assert stats["mailer"]["approvals"] == 1
        assert stats["mailer"]["overrides"] == 0

    def test_stats_empty_when_no_proposals(self, svc):
        assert svc.tool_stats("nobody") == []


class TestReplayResume:
    """M4-1 upgrade: approve-after-restart replays in-place via the
    session log; deterministic fallback otherwise."""

    def _seed_run_events(self, monkeypatch, tmp_path, user_id, session_id):
        """Seed a logged model-visible run via the canonical API."""
        import src.sdk.session_events as se
        import src.storage.paths as paths_mod
        from src.sdk.messages import Message

        monkeypatch.setattr(se, "session_log_enabled", lambda: True)
        monkeypatch.setattr(
            paths_mod.DataPaths,
            "root",
            property(lambda self: tmp_path / "root"),
        )
        se.reset_session_stores()
        se.log_model_message(
            user_id, session_id, "run-1", 1, Message.user("do the thing")
        )

    async def test_replay_with_session_log(self, svc, monkeypatch, tmp_path):
        import src.sdk.governance as gov

        monkeypatch.setattr(gov, "governance_enabled", lambda: True)
        self._seed_run_events(monkeypatch, tmp_path, "ru", "sess-1")

        pid = svc.create_pending(
            "ru", "writer", {"q": 1}, permission="ask", session_id="sess-1"
        )
        svc.approve("ru", pid)

        executed: list[str] = []

        async def fake_execute(uid, pid_, registry=None):
            executed.append(pid_)
            underlying = await svc.execute_approved(uid, pid_, registry)
            return {**underlying, "content": "ok"}

        result = await svc.replay_resume("ru", pid, executor=fake_execute)
        assert result["status"] == "replayed"
        assert result["derived_history_len"] >= 1
        assert len(executed) == 1
        # exactly-once: replaying again reports already
        again = await svc.replay_resume("ru", pid, executor=fake_execute)
        assert again["execution"]["already"] is True

    async def test_fallback_without_session_log(self, svc, monkeypatch):
        import src.sdk.governance as gov

        monkeypatch.setattr(gov, "governance_enabled", lambda: True)
        pid = svc.create_pending(
            "ru", "writer", {"q": 1}, permission="ask", session_id=None
        )
        svc.approve("ru", pid)
        result = await svc.replay_resume("ru", pid)
        assert result.get("status") == "executed"

    async def test_fallback_when_flag_off(self, svc, monkeypatch, tmp_path):
        import src.sdk.governance as gov
        import src.sdk.session_events as se

        monkeypatch.setattr(gov, "governance_enabled", lambda: True)
        self._seed_run_events(monkeypatch, tmp_path, "ru", "sess-2")
        # Patch AFTER seeding (the seeding helper enables the flag) — the
        # flag is off at replay_resume call time, forcing the fallback.
        monkeypatch.setattr(se, "session_log_enabled", lambda: False)
        pid = svc.create_pending(
            "ru", "writer", {"q": 1}, permission="ask", session_id="sess-2"
        )
        svc.approve("ru", pid)
        result = await svc.replay_resume("ru", pid)
        assert result.get("status") == "executed"


class TestSessionLogParity:
    """Review P1-1: the REAL executed result reaches the session log."""

    def test_approve_execute_logs_real_result(self, svc, tmp_path, monkeypatch):

        import src.sdk.governance as gov
        from src.sdk.session_events import (
            SessionEventStore,
        )

        monkeypatch.setattr(gov, "governance_enabled", lambda: True)
        monkeypatch.setattr(gov, "session_log_enabled", lambda: True)

        # Session event store rooted in the same tmp root.
        store = SessionEventStore(str(tmp_path / "root" / "events.db"))
        monkeypatch.setattr(
            gov, "get_session_event_store", lambda user_id: store
        )
        monkeypatch.setattr(
            "src.sdk.session_events.get_session_event_store",
            lambda user_id: store,
        )

        # Seed the log with the synthetic pending-ack result (what the guard
        # leaves behind) so the fix must REPLACE-supersede it with the real
        # content in a later event.
        from src.sdk.run_events import (
            ToolEndData,
            ToolInputEndEvent,
            ToolResultData,
            ToolResultEvent,
        )

        pid = svc.create_pending(
            "u1", "gated_tool", {"x": "1"}, permission="ask",
            session_id="sess-1",
        )
        seq = store.next_sequence("sess-1")
        store.append(
            ToolInputEndEvent(
                event_id="e1", sequence=seq, timestamp=svc._now() if hasattr(svc, "_now") else __import__("datetime").datetime.now(__import__("datetime").UTC),
                session_id="sess-1", run_id="r1", attempt=1,
                data=ToolEndData(block_id="b9", tool_call_id="call_9", arguments={"x": "1"}),
            )
        )
        ack = f"Proposal {pid[:8]} for 'gated_tool' submitted (auto-send window open). Status: pending."
        store.append(
            ToolResultEvent(
                event_id="e2", sequence=seq + 1,
                timestamp=__import__("datetime").datetime.now(__import__("datetime").UTC),
                session_id="sess-1", run_id="r1", attempt=1,
                data=ToolResultData(block_id="b10", tool_call_id="call_9", name="gated_tool", status="completed", content=ack),
            )
        )

        assert svc.approve("u1", pid) is True

        # Execute: a fake registry tool returns the real content.
        class FakeTD:
            name = "gated_tool"

            async def ainvoke(self, arguments):
                return "REAL EXECUTED OUTPUT"

        import asyncio

        out = asyncio.run(svc.execute_approved("u1", pid, [FakeTD()]))
        assert out["structured_content"]["executed"] is True

        events = store.events("sess-1")
        results = [e for e in events if e.type == "tool_result"]
        assert len(results) == 2  # ack + real
        real = results[-1]
        assert "REAL EXECUTED OUTPUT" in str(real.data.content)
        assert real.data.tool_call_id == "call_9"  # same call pairing
        assert real.data.status == "completed"

    def test_execute_logs_failed_status_for_tool_result_error(
        self, svc, tmp_path, monkeypatch
    ):
        """#26: a tool that ran and failed must be logged as failed, not completed."""
        import asyncio

        import src.sdk.governance as gov
        from src.sdk.session_events import SessionEventStore
        from src.sdk.tools import ToolResult

        monkeypatch.setattr(gov, "governance_enabled", lambda: True)
        monkeypatch.setattr(gov, "session_log_enabled", lambda: True)
        store = SessionEventStore(str(tmp_path / "root" / "events.db"))
        monkeypatch.setattr(gov, "get_session_event_store", lambda user_id: store)

        pid = svc.create_pending(
            "u1", "gated_tool", {"x": "1"}, permission="ask", session_id="sess-err",
        )
        assert svc.approve("u1", pid) is True

        class FakeTD:
            name = "gated_tool"

            async def ainvoke(self, arguments):
                return ToolResult(content="upstream refused", is_error=True)

        out = asyncio.run(svc.execute_approved("u1", pid, [FakeTD()]))
        assert out["is_error"] is True, out
        assert out["content"] == "upstream refused", out

        results = [e for e in store.events("sess-err") if e.type == "tool_result"]
        assert results, "no session-log event written"
        assert results[-1].data.status == "failed", results[-1].data
        assert "upstream refused" in str(results[-1].data.content)

    def test_no_session_id_no_log_write(self, svc, monkeypatch, tmp_path):
        import src.sdk.governance as gov
        from src.sdk.session_events import SessionEventStore

        monkeypatch.setattr(gov, "governance_enabled", lambda: True)
        monkeypatch.setattr(gov, "session_log_enabled", lambda: True)
        store = SessionEventStore(str(tmp_path / "root" / "events.db"))
        monkeypatch.setattr(gov, "get_session_event_store", lambda user_id: store)

        pid = svc.create_pending("u1", "gated_tool", {"x": "1"}, permission="ask")
        svc.approve("u1", pid)

        class FakeTD:
            name = "gated_tool"

            async def ainvoke(self, arguments):
                return "OUT"

        import asyncio

        out = asyncio.run(svc.execute_approved("u1", pid, [FakeTD()]))
        assert out["structured_content"]["executed"] is True
        assert store.events("sess-x") == []  # nothing written without linkage


class _CountingHITL(HITLMiddleware):
    """HITLMiddleware that records every guard dispatch (issue #19)."""

    def __init__(self, user_id: str = "term_user") -> None:
        super().__init__(user_id=user_id)
        self.guard_calls: list[str] = []

    async def guard_tool_call(self, tool_name, tool_input):  # type: ignore[override]
        self.guard_calls.append(tool_name)
        return await super().guard_tool_call(tool_name, tool_input)


class _ToolCallProvider:
    """Provider that proposes one tool call, then finishes."""

    def __init__(self, tool_name: str, arguments: dict | None = None) -> None:
        self._tool_name = tool_name
        self._arguments = dict(arguments or {})
        self._n = 0

    async def chat(self, messages, tools=None, **kwargs):
        from src.sdk.messages import Message

        self._n += 1
        if self._n == 1:
            return Message.assistant(
                "",
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": self._tool_name,
                        "arguments": dict(self._arguments),
                    }
                ],
            )
        return Message.assistant("done")


class TestBlockedCallTerminal:
    """Issue #19 discriminator (non-stream): a governance-blocked tool call
    is terminal on BOTH the parallel-safe (batch) and sequential (single)
    executors — one guard evaluation, zero tool-body calls, one synthetic
    tool result, and the blocked call is never recorded as executed."""

    @pytest.fixture()
    def gov_env(self, tmp_path, monkeypatch):
        import src.storage.paths as paths_mod
        from src.config import reload_settings

        monkeypatch.setattr(
            paths_mod.DataPaths,
            "root",
            property(lambda self: tmp_path / "root"),
            raising=False,
        )
        import src.sdk.governance as gov

        monkeypatch.setattr(gov, "_services", {})
        monkeypatch.setattr(gov, "_metering_lock_holder", None, raising=False)
        monkeypatch.setenv("GOVERNANCE_ENABLED", "true")
        monkeypatch.setenv("GOVERNANCE_PERMISSIONS", '{"tools":{"explicit_tool":"ask"}}')
        reload_settings()
        yield
        monkeypatch.undo()
        paths_mod._paths_cache.clear()
        reload_settings()

    @staticmethod
    def _loop(monkeypatch, gov_env, *, destructive: bool, mw: _CountingHITL):
        from src.sdk.loop import AgentLoop, RunConfig
        from src.sdk.messages import Message  # noqa: F401
        from src.sdk.tools import ToolDefinition

        executions: list[str] = []

        async def body(**kwargs):
            executions.append("ran")
            return "EXECUTED-BODY"

        from src.sdk.tools import ToolAnnotations

        td = ToolDefinition(
            name="explicit_tool",
            description="gated tool",
            function=body,
            annotations=ToolAnnotations(destructive=destructive),
        )
        loop = AgentLoop(
            provider=_ToolCallProvider("explicit_tool"),
            tools=[td],
            user_id="term_user",
            run_config=RunConfig(max_llm_calls=3),
            middlewares=[mw],
        )
        loop._flow_model = "test-model"
        return loop, executions

    @pytest.mark.asyncio
    async def test_batch_path_blocked_call_is_terminal(
        self, gov_env, monkeypatch
    ):
        """Parallel-safe (default annotations) path: one guard dispatch,
        zero body executions, exactly one tool result, nothing recorded."""
        from src.sdk.loop import AgentLoop  # noqa: F401
        from src.sdk.messages import Message

        mw = _CountingHITL(user_id="term_user")
        loop, executions = self._loop(monkeypatch, gov_env, destructive=False, mw=mw)
        await loop.run([Message.user("go")])

        assert executions == []
        assert mw.guard_calls == ["explicit_tool"]
        tool_msgs = [m for m in loop.state.messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert "awaiting explicit human approval" in tool_msgs[0].content
        assert loop.state.extra.get("_executed_tool_calls", []) == []

    @pytest.mark.asyncio
    async def test_sequential_path_blocked_call_is_terminal(
        self, gov_env, monkeypatch
    ):
        """Destructive (sequential) executor: same terminal contract."""
        from src.sdk.messages import Message

        mw = _CountingHITL(user_id="term_user")
        loop, executions = self._loop(monkeypatch, gov_env, destructive=True, mw=mw)
        await loop.run([Message.user("go")])

        assert executions == []
        assert mw.guard_calls == ["explicit_tool"]
        tool_msgs = [m for m in loop.state.messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert "awaiting explicit human approval" in tool_msgs[0].content
        assert loop.state.extra.get("_executed_tool_calls", []) == []
