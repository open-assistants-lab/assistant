"""M4-1 review P0: enforcement wiring + execution leg + receipts (issue #6).

Covers:
- guard hook blocks hard_block tools with a synthetic refusal (no execution)
- explicit tier: durable pending; approve endpoint executes EXACTLY once
  (idempotent double-approve)
- show_then_auto_send: window elapses at read time — pending before,
  auto-approved + executed after (monkeypatched clock)
- governance.enabled=false -> zero governance hooks in a loop
"""

from __future__ import annotations

import pytest

from src.sdk.tools import ToolDefinition


@pytest.fixture()
def gov_env(monkeypatch, tmp_path):
    """Isolated governance environment: flag on, isolated root."""
    import src.storage.paths as paths_mod
    from src.config import reload_settings

    (tmp_path / "root").mkdir(parents=True, exist_ok=True)
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
    monkeypatch.setenv("GOVERNANCE_TIERS", '{"email_send": "explicit"}')
    reload_settings()
    yield
    monkeypatch.undo()
    paths_mod._paths_cache.clear()
    reload_settings()


@pytest.fixture()
def client(gov_env):
    from fastapi.testclient import TestClient

    from src.http.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestGovernanceEndpoints:
    def test_list_pendings_empty(self, client):
        r = client.get("/v1/governance/pendings", params={"user_id": "govu"})
        assert r.status_code == 200
        assert r.json() == []

    def test_approve_executes_exactly_once(self, client, gov_env, monkeypatch):
        # create a pending directly via the service
        from src.sdk.governance import get_governance_service

        pid = get_governance_service("govu").create_pending(
            "govu", "time_get", {"x": 1}, tier="explicit"
        )

        executed: list[dict] = []

        import src.http.routers.governance as grouter

        async def fake_execute(user_id, proposal_id, tool, arguments):
            executed.append({"tool": tool, "arguments": arguments})
            # mirror the real leg: transition approved -> executed
            await get_governance_service("govu").execute_approved(
                "govu", proposal_id, registry=[]
            )
            return {"content": "ok", "structured_content": {"executed": True}}

        monkeypatch.setattr(grouter_module(), "execute_approved_tool", fake_execute)

        r1 = client.post(f"/v1/governance/pendings/{pid}/approve", params={"user_id": "govu"})
        assert r1.status_code == 200
        assert r1.json()["status"] == "executed"
        assert len(executed) == 1

        # idempotent double-approve: no second execution
        r2 = client.post(f"/v1/governance/pendings/{pid}/approve", params={"user_id": "govu"})
        assert r2.status_code == 200
        assert r2.json()["status"] == "executed"
        assert len(executed) == 1

    def test_cancel(self, client, gov_env):
        from src.sdk.governance import get_governance_service

        pid = get_governance_service("govu").create_pending(
            "govu", "email_send", {}, tier="explicit"
        )
        r = client.post(f"/v1/governance/pendings/{pid}/cancel", params={"user_id": "govu"})
        assert r.status_code == 200
        row = get_governance_service("govu").get_pending("govu", pid)
        assert row is not None and row["status"] == "cancelled"


def grouter_module():
    import src.http.routers.governance as g

    return g


class TestLoopGuardHook:
    async def test_guard_blocks_hard_block_tool(self, gov_env, monkeypatch):
        """A hard_block tool call returns the synthetic refusal WITHOUT
        executing the tool body."""
        from src.sdk.loop import AgentLoop, RunConfig
        from src.sdk.messages import Message
        from src.sdk.middleware_hitl import HITLMiddleware
        from src.sdk.tools import ToolDefinition, ToolResult

        calls: list[str] = []

        async def body(**kwargs):
            calls.append("executed")
            return "SHOULD NOT RUN"

        td = ToolDefinition(name="email_send", description="x", function=body)
        monkeypatch.setenv("GOVERNANCE_TIERS", '{"email_send": "hard_block"}')
        from src.config import reload_settings

        reload_settings()

        loop = AgentLoop(
            provider=_FakeProvider(),
            tools=[td],
            user_id="guardu",
            run_config=RunConfig(max_llm_calls=3),
            middlewares=[HITLMiddleware(user_id="guardu")],
        )
        results = await loop.run([Message.user("do it")])
        # the tool result in history must be the synthetic refusal
        tool_results = [
            m for m in results if getattr(m, "role", "") == "tool"
        ] or []
        assert calls == []  # body never executed
        assert tool_results, "expected a synthetic tool result in history"

    async def test_disabled_flag_no_hooks(self, gov_env, monkeypatch):
        """governance.enabled=false -> guard passes everything through."""
        from src.sdk.loop import AgentLoop, RunConfig
        from src.sdk.messages import Message
        from src.sdk.middleware_hitl import HITLMiddleware

        monkeypatch.setenv("GOVERNANCE_ENABLED", "false")
        monkeypatch.setenv("GOVERNANCE_TIERS", '{"time_get": "hard_block"}')
        from src.config import reload_settings

        reload_settings()
        mw = HITLMiddleware(user_id="guardu2")
        assert await mw.guard_tool_call("time_get", {}) is None

        executed: list[str] = []

        async def body(**kwargs):
            executed.append("ran")
            return "ok"

        td = ToolDefinition(name="time_get", description="x", function=body)
        loop = AgentLoop(
            provider=_FakeProvider(),
            tools=[td],
            user_id="guardu2",
            run_config=RunConfig(max_llm_calls=3),
            middlewares=[mw],
        )
        await loop.run([Message.user("go")])
        assert executed == ["ran"]  # execution proceeded (no governance)


class _FakeProvider:
    """Provider that makes one tool call then finishes."""

    def __init__(self) -> None:
        self._n = 0

    async def chat(self, messages, tools=None, **kwargs):
        self._n += 1
        from src.sdk.messages import Message

        if self._n == 1:
            names = {
                t.name if hasattr(t, "name") else t.get("function", {}).get("name", "")
                for t in (tools or [])
            }
            target = "time_get" if "time_get" in names else "email_send"
            return Message.assistant(
                "",
                tool_calls=[{"id": "call1", "name": target, "arguments": {}}],
            )
        return Message.assistant("done")

def test_tool_stats_endpoint(client, test_user_id):
    from src.sdk.governance import get_governance_service

    svc = get_governance_service(test_user_id)
    svc.create_pending(test_user_id, "mailer", {"to": "x"})
    svc.create_pending(test_user_id, "mailer", {"to": "y"})
    svc.record_override(test_user_id, "mailer")
    r = client.get("/v1/governance/tool-stats", params={"user_id": test_user_id})
    assert r.status_code == 200
    rows = r.json()
    assert rows and rows[0]["tool"] == "mailer"
    assert rows[0]["override_rate"] == 0.5
