"""Acceptance: installed commands use real HITL, receipts and local fixture state."""
import json

from examples.jen_reference.fixture import read_store, set_pause
from src.config import reload_settings
from src.sdk.governance import GovernanceService
from src.sdk.loop import AgentLoop, RunConfig
from src.sdk.messages import Message
from src.sdk.middleware_hitl import HITLMiddleware
from src.sdk.tools import ToolResult
from src.sdk.tools_custom import get_custom_tools
from tests.integration.fake_provider import FakeProvider

USER = "default_user"
WRITE = "fixture_store_pause"
ARGS = {"store_id": "fixture-alpha", "paused": "true", "expected_revision": 1}


async def propose(harness, name=WRITE, args=None):
    paths, tools, svc, db = harness
    loop = AgentLoop(provider=FakeProvider([{"tool_calls": [{"id": "fixture-call", "name": name,
                        "arguments": args or ARGS}]}, {"content": "done"}]),
                     tools=tools, user_id=USER, run_config=RunConfig(max_llm_calls=3),
                     middlewares=[HITLMiddleware(user_id=USER)])
    messages = await loop.run([Message.user("synthetic fixture operation")])
    return svc.list_pending_ids(USER), messages


async def approve_and_execute(harness, pid, tools=None):
    _, registry, svc, _ = harness
    svc.approve(USER, pid)
    return await svc.execute_approved(USER, pid, registry=tools or registry)


async def test_read_runs_without_approval(harness):
    ids, messages = await propose(harness, "fixture_store_read", {"store_id": "fixture-alpha"})
    assert ids == []
    outputs = [m.content for m in messages if m.role == "tool"]
    assert any('"revision": 1' in str(o) and '"outcome": "read"' in str(o) for o in outputs)


async def test_installed_read_failure_has_failed_tool_outcome(harness):
    read = next(t for t in harness[1] if t.name == "fixture_store_read")
    result = await read.ainvoke({"store_id": "alpha"})
    assert isinstance(result, ToolResult)
    assert result.is_error and result.outcome.value == "failed"
    assert "unknown_target" in result.content
    assert read_store(harness[3], "fixture-alpha")["revision"] == 1


async def test_write_creates_real_pending_without_mutation(harness):
    ids, _ = await propose(harness)
    assert len(ids) == 1
    _, _, svc, db = harness
    row = svc.get_pending(USER, ids[0])
    assert row["status"] == "pending" and row["arguments"] == ARGS
    assert row["definition_hash"]
    assert read_store(db, "fixture-alpha")["paused"] is False


async def test_rejection_leaves_state_unchanged(harness):
    ids, _ = await propose(harness)
    _, tools, svc, db = harness
    svc.cancel(USER, ids[0])
    await svc.execute_approved(USER, ids[0], registry=tools)
    assert svc.get_pending(USER, ids[0])["status"] != "approved"
    assert read_store(db, "fixture-alpha")["revision"] == 1


async def test_approval_executes_once(harness):
    ids, _ = await propose(harness)
    result = await approve_and_execute(harness, ids[0])
    _, tools, svc, db = harness
    assert result["outcome"] == "succeeded"
    assert svc.get_pending(USER, ids[0])["outcome"] == "succeeded"
    assert read_store(db, "fixture-alpha")["paused"] is True
    svc.approve(USER, ids[0])
    repeat = await svc.execute_approved(USER, ids[0], registry=tools)
    assert repeat["already"] is True
    assert read_store(db, "fixture-alpha")["revision"] == 2
    assert read_store(db, "fixture-beta")["revision"] == 1


async def test_changed_definition_refuses_execution(harness):
    ids, _ = await propose(harness)
    paths, _, svc, db = harness
    file = paths.data / "Tools/fixture_store_pause/TOOL.md"
    file.write_text(file.read_text().replace("--paused {{paused}}", "--paused false"))
    result = await approve_and_execute(harness, ids[0], get_custom_tools(USER))
    assert result["outcome"] == "failed"
    assert svc.get_pending(USER, ids[0])["outcome"] == "failed"
    assert read_store(db, "fixture-alpha")["paused"] is False


async def test_state_changed_after_proposal_refuses_write(harness):
    ids, _ = await propose(harness)
    _, _, svc, db = harness
    set_pause(db, "fixture-alpha", True, 1)
    result = await approve_and_execute(harness, ids[0])
    assert result["outcome"] == "failed"
    assert svc.get_pending(USER, ids[0])["outcome"] == "failed"
    assert "stale_revision" in result["content"]
    assert read_store(db, "fixture-alpha")["revision"] == 2


async def test_policy_drift_refuses_execution(harness, monkeypatch):
    ids, _ = await propose(harness)
    monkeypatch.setenv("GOVERNANCE_PERMISSIONS", json.dumps({"tools": {WRITE: "deny"}}))
    reload_settings()
    result = await approve_and_execute(harness, ids[0], get_custom_tools(USER))
    assert result["outcome"] == "refused"
    assert read_store(harness[3], "fixture-alpha")["paused"] is False


async def test_other_instance_cannot_use_pending(harness, tmp_path):
    ids, _ = await propose(harness)
    other = GovernanceService(data_root=str(tmp_path / "other/data"))
    assert other.get_pending(USER, ids[0]) is None
    assert await other.execute_approved(USER, ids[0], registry=harness[1]) == {"status": "missing"}
    assert read_store(harness[3], "fixture-alpha")["paused"] is False
