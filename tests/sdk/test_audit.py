"""CaptureBus / AuditStore / loop-boundary audit capture tests (roadmap P0-T3).

RED phase: these assert the audit contract before the implementation exists.
"""


import pytest

from src.sdk.audit import AuditEvent, AuditStore, CaptureBus, default_capture_bus
from src.sdk.messages import Message, ToolCall
from src.sdk.tools import ToolAnnotations, tool

# ── CaptureBus ────────────────────────────────────────────────────────────────


class _Sink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def __call__(self, event: AuditEvent) -> None:
        self.events.append(event)


def test_capture_bus_delivers_to_subscribers():
    bus = CaptureBus()
    sink_a, sink_b = _Sink(), _Sink()
    bus.subscribe(sink_a)
    bus.subscribe(sink_b)
    event = AuditEvent(kind="tool_call", tool="echo", call_id="c1")
    bus.emit(event)
    assert sink_a.events == [event]
    assert sink_b.events == [event]


def test_capture_bus_swallows_sink_errors():
    """emit() must never break control flow (emit-only by contract)."""

    def bad_sink(_event):
        raise RuntimeError("boom")

    bus = CaptureBus()
    bus.subscribe(bad_sink)
    ok = _Sink()
    bus.subscribe(ok)
    bus.emit(AuditEvent(kind="tool_call", tool="echo", call_id="c1"))
    assert len(ok.events) == 1


def test_default_bus_is_singleton():
    assert default_capture_bus is default_capture_bus


# ── AuditEvent ────────────────────────────────────────────────────────────────


def test_audit_event_fields():
    ev = AuditEvent(
        kind="approve",
        tool="email_send",
        call_id="call_9",
        user_id="u1",
        session_id="s1",
        approved=True,
        detail="ok",
    )
    assert ev.kind == "approve"
    assert ev.tool == "email_send"
    assert ev.call_id == "call_9"
    assert ev.approved is True
    assert ev.event_id
    assert ev.ts is not None


def test_audit_event_kind_validated():
    with pytest.raises(ValueError):
        AuditEvent(kind="nope", tool="echo")  # type: ignore[arg-type]


# ── AuditStore (append-only SQLite) ───────────────────────────────────────────


def test_audit_store_record_and_export(tmp_path):
    store = AuditStore(str(tmp_path / "audit.db"))
    ev = AuditEvent(kind="tool_call", tool="echo", call_id="c1", user_id="u1", session_id="s1")
    store.record(ev)
    rows = store.export(user_id="u1")
    assert len(rows) == 1
    assert rows[0].kind == "tool_call"
    assert rows[0].tool == "echo"
    assert rows[0].call_id == "c1"


def test_audit_store_is_append_only(tmp_path):
    store = AuditStore(str(tmp_path / "audit.db"))
    ev = AuditEvent(kind="tool_result", tool="echo", call_id="c1", user_id="u1")
    store.record(ev)
    store.record(ev)  # same id twice → append-only means no-op overwrite
    assert len(store.export(user_id="u1")) == 1


def test_audit_store_no_update_path(tmp_path):
    store = AuditStore(str(tmp_path / "audit.db"))
    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")
    assert not hasattr(store, "upsert")


def test_audit_store_export_filters_user_and_since(tmp_path):
    store = AuditStore(str(tmp_path / "audit.db"))
    store.record(AuditEvent(kind="tool_call", tool="a", call_id="c1", user_id="u1"))
    store.record(AuditEvent(kind="tool_call", tool="b", call_id="c2", user_id="u2"))
    assert [r.tool for r in store.export(user_id="u1")] == ["a"]
    since = store.export(user_id="u1")[0].ts
    assert store.export(user_id="u1", since=since)  # includes >= since


def test_audit_store_subscribes_to_bus(tmp_path):
    store = AuditStore(str(tmp_path / "audit.db"))
    bus = CaptureBus()
    bus.subscribe(store.record)
    bus.emit(AuditEvent(kind="tool_call", tool="echo", call_id="c1", user_id="u1"))
    assert len(store.export(user_id="u1")) == 1


# ── Loop boundary emission ────────────────────────────────────────────────────


class _FakeProvider:
    """Minimal provider that returns canned responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self._i = 0

    async def chat(self, messages, tools=None, model=None, **kwargs):
        resp = self.responses[self._i]
        self._i += 1
        return resp

    async def chat_stream_impl(self, messages, tools, model, **kwargs):
        yield Message.assistant(content="done")

    @property
    def model(self):
        return "fake:model"

    @property
    def provider_id(self):
        return "fake"


@tool
def echo(text: str = "hello") -> str:
    return f"echo:{text}"


@pytest.mark.asyncio
async def test_loop_emits_tool_call_and_result(tmp_path):
    from src.sdk.loop import AgentLoop

    store = AuditStore(str(tmp_path / "audit.db"))
    bus = CaptureBus()
    bus.subscribe(store.record)

    provider = _FakeProvider(
        responses=[
            Message.assistant(
                content="",
                tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "x"})],
            ),
            Message.assistant(content="done"),
        ]
    )
    loop = AgentLoop(provider=provider, tools=[echo], capture_bus=bus, user_id="audit_user")
    await loop.run([Message.user("go")])

    kinds = [e.kind for e in store.export(user_id="audit_user")]
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    call_row = [e for e in store.export(user_id="audit_user") if e.kind == "tool_call"][0]
    assert call_row.tool == "echo"
    assert call_row.call_id == "call_1"


@pytest.mark.asyncio
async def test_loop_approve_emits_row(tmp_path):
    from src.sdk.loop import AgentLoop

    store = AuditStore(str(tmp_path / "audit.db"))
    bus = CaptureBus()
    bus.subscribe(store.record)

    loop = AgentLoop(
        provider=_FakeProvider([]), tools=[echo], capture_bus=bus, user_id="audit_user"
    )
    loop.approve_tool_call(ToolCall(id="call_2", name="echo", arguments={"text": "y"}))
    rows = store.export(user_id="audit_user")
    assert len(rows) == 1
    assert rows[0].kind == "approve"
    assert rows[0].approved is True
    assert rows[0].call_id == "call_2"


@pytest.mark.asyncio
async def test_loop_interrupt_emits_row(tmp_path):
    from src.sdk.loop import AgentLoop

    store = AuditStore(str(tmp_path / "audit.db"))
    bus = CaptureBus()
    bus.subscribe(store.record)

    @tool
    def dangerous(cmd: str = "rm -rf /") -> str:
        return f"ran:{cmd}"

    dangerous.annotations = ToolAnnotations(destructive=True, read_only=False)

    provider = _FakeProvider(
        responses=[
            Message.assistant(
                content="",
                tool_calls=[ToolCall(id="call_3", name="dangerous", arguments={"cmd": "x"})],
            ),
            Message.assistant(content="done"),
        ]
    )
    loop = AgentLoop(provider=provider, tools=[dangerous], capture_bus=bus, user_id="audit_user")
    # HITL is dormant for ship (_should_interrupt returns False); patch the
    # gate to exercise the interrupt emission path itself.
    loop._should_interrupt = lambda tc: tc.name == "dangerous"  # type: ignore[method-assign]
    await loop.run([Message.user("go")])

    rows = store.export(user_id="audit_user")
    interrupt_rows = [r for r in rows if r.kind == "interrupt"]
    assert interrupt_rows, f"expected interrupt rows, got {[r.kind for r in rows]}"
    assert interrupt_rows[0].tool == "dangerous"
    assert interrupt_rows[0].approved is False


# ── Production wiring (P0-T3 fix round) ───────────────────────────────────────


class _FakePaths:
    def __init__(self, root):
        self._root = root

    def audit_dir(self):
        p = self._root / "Audit"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def audit_db(self):
        return self.audit_dir() / "audit.db"


def test_wiring_subscribes_default_bus_store(tmp_path, monkeypatch):
    """ensure_audit_store_subscribed wires a store that persists bus events."""
    from src.sdk import audit as audit_mod

    monkeypatch.setattr(audit_mod, "DataPaths", lambda **kw: _FakePaths(tmp_path))
    store = audit_mod.ensure_audit_store_subscribed("wire_user")
    audit_mod.default_capture_bus.emit(
        AuditEvent(kind="tool_call", tool="echo", call_id="w1", user_id="wire_user")
    )
    rows = store.export(user_id="wire_user")
    assert len(rows) == 1
    assert rows[0].call_id == "w1"
    assert (tmp_path / "Audit" / "audit.db").exists()


def test_wiring_is_idempotent_per_user(tmp_path, monkeypatch):
    from src.sdk import audit as audit_mod

    monkeypatch.setattr(audit_mod, "DataPaths", lambda **kw: _FakePaths(tmp_path))
    s1 = audit_mod.ensure_audit_store_subscribed("wire_user2")
    s2 = audit_mod.ensure_audit_store_subscribed("wire_user2")
    assert s1 is s2


@pytest.mark.asyncio
async def test_wiring_persists_loop_roundtrip_through_default_bus(tmp_path, monkeypatch):
    """A loop using the default bus persists rows through the wired store."""
    from src.sdk import audit as audit_mod
    from src.sdk.loop import AgentLoop

    monkeypatch.setattr(audit_mod, "DataPaths", lambda **kw: _FakePaths(tmp_path))
    store = audit_mod.ensure_audit_store_subscribed("wire_user3")

    provider = _FakeProvider(
        responses=[
            Message.assistant(
                content="",
                tool_calls=[ToolCall(id="call_9", name="echo", arguments={"text": "z"})],
            ),
            Message.assistant(content="done"),
        ]
    )
    # No explicit capture_bus: production default is default_capture_bus.
    loop = AgentLoop(provider=provider, tools=[echo], user_id="wire_user3")
    await loop.run([Message.user("go")])

    kinds = [e.kind for e in store.export(user_id="wire_user3")]
    assert "tool_call" in kinds
    assert "tool_result" in kinds


@pytest.mark.asyncio
async def test_direct_loop_construction_sites_wire_audit_store(tmp_path, monkeypatch):
    """Coordinator _run_loop (bypassing create_sdk_loop) wires the audit store.

    Roadmap P0-T3 follow-up: loops built directly must still subscribe the
    per-user audit store so subagent/research runs persist audit rows.
    """
    from src.sdk import audit as audit_mod
    from src.sdk import coordinator as coord_mod
    from src.sdk.subagent_context import SubagentContext

    calls: list[str] = []
    _original_ensure = audit_mod.ensure_audit_store_subscribed

    def _recorder(user_id: str):
        calls.append(user_id)
        monkeypatch.setattr(audit_mod, "DataPaths", lambda **kw: _FakePaths(tmp_path))
        return _original_ensure(user_id)

    monkeypatch.setattr(audit_mod, "ensure_audit_store_subscribed", _recorder)

    class _FakeProvider:
        async def complete(self, *a, **k):  # pragma: no cover - unused
            raise AssertionError("provider should not be called")

    class _FakeLoop:
        def __init__(self, *a, **k):
            pass

        async def run(self, messages):  # noqa: ARG002
            return []

    def _fake_create_model_from_config(*a, **k):
        return _FakeProvider()

    monkeypatch.setattr(
        "src.sdk.providers.factory.create_model_from_config",
        _fake_create_model_from_config,
    )
    monkeypatch.setattr("src.sdk.loop.AgentLoop", _FakeLoop)
    monkeypatch.setattr(coord_mod, "_build_tools_for_subagent", lambda *a, **k: [])
    monkeypatch.setattr(coord_mod, "_build_system_prompt", lambda *a, **k: "prompt")

    from agentprofile.models import AgentProfile

    coord = coord_mod.SubagentCoordinator(user_id="direct_loop_user")
    result = await coord._run_loop(
        task_id="t1",
        profile=AgentProfile(name="p", description="d", model="ollama:m"),
        task="hello",
        db=None,  # not touched on this path
        ctx=SubagentContext(),
    )

    assert calls == ["direct_loop_user"], calls
    assert result.success is True
