"""`subagent_schedule` — the agent-facing way to create a schedule (#46 Task 5).

The tool creates a schedule and nothing more: it returns a schedule ID, which is
not permission to execute. A plan that would be rejected at launch must fail
here too, so the agent learns before the job exists rather than at fire time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

RUN_AT = (datetime.now(UTC) + timedelta(hours=3)).isoformat()


@pytest.fixture(autouse=True)
def pinned_permissions(monkeypatch):
    """Never read ambient governance config; see the API test for why."""
    import src.sdk.subagent_capabilities as caps

    monkeypatch.setattr(
        caps,
        "resolve_permission",
        lambda _u, tool, _i: "ask" if tool == "shell_execute" else "allow",
    )


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    import uuid

    from src.storage.paths import DataPaths

    per_test = tmp_path / uuid.uuid4().hex
    per_test.mkdir(parents=True, exist_ok=True)
    db = DataPaths(
        data_path=str(per_test), data_root=str(per_test), user_id="alice"
    ).subagent_schedules_db_path()
    monkeypatch.setattr(
        "src.subagent.schedules_store.get_paths",
        lambda *_a, **_k: DataPaths(
            data_path=str(per_test), data_root=str(per_test), user_id="alice"
        ),
    )
    yield db


def _patch_service(monkeypatch, result=None, raises=None):
    """Stub the service so these tests exercise the tool's own contract."""
    from src.subagent import schedule_service

    calls: list[dict] = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return result or {
            "schedule_id": "sched_abc123",
            "status": "scheduled",
            "manifest_hash": "plan_hash",
            "trigger_kind": "once",
        }

    monkeypatch.setattr(schedule_service, "create_schedule", fake_create)
    return calls


def _invoke(**kwargs):
    from src.sdk.tools_core.subagent import subagent_schedule

    payload = {
        "subagent_name": "researcher",
        "task": "check the deployment",
        "user_id": "alice",
    }
    payload.update(kwargs)
    return subagent_schedule.ainvoke(payload)


# --------------------------------------------------------------------------
# surface contract
# --------------------------------------------------------------------------


def test_the_tool_is_registered_and_approval_gated() -> None:
    """Creating unattended future work is a write, so it must be gated."""
    from src.sdk.native_tools import get_native_tools
    from src.sdk.tools_core.subagent import subagent_schedule

    assert subagent_schedule.name == "subagent_schedule"
    assert subagent_schedule.annotations.destructive is True
    names = {t.name for t in get_native_tools()}
    assert "subagent_schedule" in names


def test_the_tool_exposes_both_triggers_and_a_timezone() -> None:
    from src.sdk.tools_core.subagent import subagent_schedule

    props = subagent_schedule.parameters["properties"]
    for field in ("subagent_name", "task", "run_at", "cron", "timezone", "workspace_id"):
        assert field in props, f"missing {field}"


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creating_a_schedule_returns_an_id(monkeypatch) -> None:
    calls = _patch_service(monkeypatch)
    result = await _invoke(run_at=RUN_AT)
    assert "sched_abc123" in str(result)
    assert calls[0]["user_id"] == "alice"
    assert calls[0]["subagent_name"] == "researcher"


@pytest.mark.asyncio
async def test_recurring_is_supported(monkeypatch) -> None:
    calls = _patch_service(monkeypatch)
    await _invoke(cron="30 7 * * 1-5", timezone="Europe/Berlin")
    assert calls[0]["cron"] == "30 7 * * 1-5"
    assert calls[0]["timezone"] == "Europe/Berlin"


# --------------------------------------------------------------------------
# refusals must be visible, not silent
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_not_ready_plan_is_refused_and_names_the_capability(monkeypatch) -> None:
    from src.subagent.schedule_service import ScheduleRejected

    _patch_service(
        monkeypatch,
        raises=ScheduleRejected(
            "Subagent launch would be rejected before execution, so the schedule "
            "was not created. An unattended schedule must resolve to 'allow' for "
            "every declared capability. Blocked: tool:shell_execute (permission_ask)",
            code="plan_not_ready",
            blocked=["tool:shell_execute (permission_ask)"],
        ),
    )
    result = await _invoke(run_at=RUN_AT)
    assert getattr(result, "is_error", False) is True
    assert "shell_execute" in result.content
    assert "allow" in result.content


@pytest.mark.asyncio
async def test_an_invalid_trigger_is_refused(monkeypatch) -> None:
    from src.subagent.schedule_service import ScheduleRejected

    _patch_service(
        monkeypatch,
        raises=ScheduleRejected(
            "Exactly one of run_at or cron is required", code="invalid_trigger"
        ),
    )
    result = await _invoke()
    assert getattr(result, "is_error", False) is True
    assert "run_at" in result.content


@pytest.mark.asyncio
async def test_a_refusal_never_reports_a_schedule_id(monkeypatch) -> None:
    from src.subagent.schedule_service import ScheduleRejected

    _patch_service(
        monkeypatch,
        raises=ScheduleRejected("preflight failed: boom", code="preflight_error"),
    )
    result = await _invoke(run_at=RUN_AT)
    assert getattr(result, "is_error", False) is True
    assert "sched_" not in result.content


# --------------------------------------------------------------------------
# the tool and the API share one gate
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_tool_refuses_when_the_real_plan_is_not_ready(monkeypatch) -> None:
    """End-to-end through the real service: shell_execute is ask, so refuse."""
    from agentprofile.models import AgentProfile

    class _Coord:
        def preflight(self, name):
            from src.sdk.subagent_capabilities import ToolSelectionMode, build_launch_plan

            return build_launch_plan(
                AgentProfile(name="researcher", tools=["shell_execute"]),
                "alice",
                "personal",
                ToolSelectionMode.ALLOWLIST,
            )

    monkeypatch.setattr(
        "src.subagent.schedule_service._get_coordinator", lambda *_a, **_k: _Coord()
    )
    result = await _invoke(run_at=RUN_AT)
    assert getattr(result, "is_error", False) is True
    assert "shell_execute" in result.content


@pytest.mark.asyncio
async def test_the_tool_creates_through_the_real_service_when_allowed(monkeypatch) -> None:
    from agentprofile.models import AgentProfile

    from src.sdk.subagent_capabilities import ToolSelectionMode, build_launch_plan

    class _Coord:
        def preflight(self, name):
            return build_launch_plan(
                AgentProfile(name="researcher", tools=["files_read"]),
                "alice",
                "personal",
                ToolSelectionMode.ALLOWLIST,
            )

    monkeypatch.setattr(
        "src.subagent.schedule_service._get_coordinator", lambda *_a, **_k: _Coord()
    )
    result = await _invoke(run_at=RUN_AT)
    assert "sched_" in str(result)

    from src.subagent.schedules_store import SubagentScheduleStore

    rows = await SubagentScheduleStore().list_for_user("alice")
    assert len(rows) == 1
    assert rows[0]["subagent_name"] == "researcher"
