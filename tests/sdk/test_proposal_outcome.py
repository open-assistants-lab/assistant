"""Issue #32 part 1: a proposal's headline must say what actually happened.

`execute_approved` writes `status='executed'` for every consumed proposal —
including runs that timed out, were signal-killed, or failed. `status` means
"the approval was consumed and is terminal" (`replay_resume` depends on that),
so the outcome is now recorded beside it. A consumer reading only the headline
can then tell a clean run from a killed or failed one without parsing
`structured_content`.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from src.sdk.governance import GovernanceService
from src.sdk.tool_results import raise_command_killed, raise_timeout
from src.sdk.tools import ToolDefinition, ToolResult

USER = "erin"


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod

    monkeypatch.setattr(paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root"))
    import src.sdk.governance as gov

    monkeypatch.setattr(gov, "_services", {})
    service = GovernanceService()
    monkeypatch.setattr(service, "resolve_tier", lambda *_: "explicit")
    monkeypatch.setattr(service, "_log_execution_result", lambda *_: None)
    monkeypatch.setattr(service, "_emit_receipt", lambda *a, **k: None)
    return service


def _run(svc, tool: ToolDefinition, arguments: dict | None = None):
    proposal_id = svc.create_pending(USER, tool.name, arguments or {}, tier="explicit")
    svc.approve(USER, proposal_id)
    result = asyncio.run(svc.execute_approved(USER, proposal_id, registry=[tool]))
    return proposal_id, result


def _tool(name: str = "probe", fn=None) -> ToolDefinition:
    return ToolDefinition(name=name, description="d", function=fn)


def test_successful_run_is_marked_succeeded(svc):
    proposal_id, result = _run(svc, _tool(fn=lambda **_: "fine"))

    assert result["outcome"] == "succeeded", result
    assert svc.get_pending(USER, proposal_id)["outcome"] == "succeeded"


def test_failed_tool_result_is_not_a_clean_outcome(svc):
    _, result = _run(
        svc,
        _tool(fn=lambda **_: ToolResult(content="upstream refused", is_error=True)),
    )

    assert result["outcome"] == "failed", result
    assert result["is_error"] is True


def test_timeout_and_kill_keep_their_own_outcome(svc):
    def times_out(**_kwargs):
        raise_timeout("probe", 1.0, 0.0)

    _, timed_out = _run(svc, _tool(fn=times_out))
    assert timed_out["outcome"] == "timed_out", timed_out

    def gets_killed(**_kwargs):
        raise_command_killed("probe", 9, 0.0)

    _, killed = _run(svc, _tool(fn=gets_killed))
    assert killed["outcome"] == "killed", killed


def test_unknown_tool_is_a_refusal_not_a_failure(svc):
    """A refusal is its own outcome: the tool never ran, but nothing broke."""
    proposal_id = svc.create_pending(USER, "not_registered", {}, tier="explicit")
    svc.approve(USER, proposal_id)

    result = asyncio.run(svc.execute_approved(USER, proposal_id, registry=[]))

    assert result["outcome"] == "refused", result
    assert svc.get_pending(USER, proposal_id)["outcome"] == "refused"


def test_unexpected_exception_is_a_failure(svc):
    def explodes(**_kwargs):
        raise RuntimeError("disk on fire")

    _, result = _run(svc, _tool(fn=explodes))

    assert result["outcome"] == "failed", result
    assert result["is_error"] is True


def test_status_stays_terminal_for_replay(svc):
    """The vocabulary change must not disturb exactly-once replay semantics."""
    proposal_id, result = _run(svc, _tool(fn=lambda **_: "fine"))

    assert svc.get_pending(USER, proposal_id)["status"] == "executed"
    again = asyncio.run(svc.execute_approved(USER, proposal_id, registry=[]))
    assert again == {"status": "executed", "already": True}


def test_bare_timeout_expired_also_persists_the_outcome(svc):
    """A TimeoutExpired without an output marker is still a timeout.

    The helper `raise_timeout` embeds its marker in `exc.output`; a bare
    `TimeoutExpired` does not, and the receipt must still say `timed_out`
    rather than falling back to a generic failure.
    """

    def times_out(**_kwargs):
        raise subprocess.TimeoutExpired("probe", 1.0)

    proposal_id, result = _run(svc, _tool(fn=times_out))

    assert result["outcome"] == "timed_out", result
    row = svc.get_pending(USER, proposal_id)
    assert row["outcome"] == "timed_out"
    assert row["status"] == "executed"


def test_existing_databases_gain_the_column(tmp_path, monkeypatch):
    """Deployments upgrading in place must not lose their ledger.

    Rows written before this change have no outcome, so they read as None —
    "unknown" rather than a claim of success.
    """
    import sqlite3

    db_dir = tmp_path / "root" / "private" / "governance" / USER
    db_dir.mkdir(parents=True)
    db = db_dir / "governance.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE proposals (proposal_id TEXT PRIMARY KEY, ts TEXT NOT NULL,"
        " tool TEXT NOT NULL, arguments TEXT NOT NULL, tier TEXT NOT NULL,"
        " status TEXT NOT NULL, expires_at TEXT, session_id TEXT, user_id TEXT,"
        " executor_json TEXT)"
    )
    conn.execute(
        "INSERT INTO proposals VALUES"
        " ('legacy','t','demo_tool','{}','explicit','executed',NULL,NULL,?,NULL)",
        (USER,),
    )
    conn.commit()
    conn.close()

    import src.storage.paths as paths_mod

    monkeypatch.setattr(
        paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
    )
    import src.sdk.governance as gov

    monkeypatch.setattr(gov, "_services", {})
    service = GovernanceService()

    columns = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(proposals)")]
    assert "outcome" not in columns, "precondition: legacy schema without the column"

    row = service.get_pending(USER, "legacy")

    columns = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(proposals)")]
    assert "outcome" in columns, columns
    assert row["status"] == "executed"
    assert row["outcome"] is None, "a legacy row must not claim an outcome"
