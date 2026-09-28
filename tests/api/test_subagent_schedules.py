"""Governed HTTP surface for subagent schedules (#46, Task 4).

A schedule is only creatable from a *ready* frozen launch plan, so an
unattended job can never be registered that nobody can answer an approval for.
The feature is dark unless `scheduling.subagent_enabled` is set.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI

RUN_AT = (datetime.now(UTC) + timedelta(hours=3)).isoformat()


@pytest.fixture
def client():
    from src.http.routers import subagent_schedules

    app = FastAPI()
    app.include_router(subagent_schedules.router)
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Bind the store the router uses to a temp root.

    Patching `get_paths` is not enough here: it is resolved at call time through
    several module references, and a test that half-wired it wrote real rows
    into the working tree's data/ directory. Binding the store class directly
    makes the router and the test's own read-back share one unambiguous path.
    """
    from src.http.routers import subagent_schedules as router_module
    from src.storage.paths import DataPaths
    from src.subagent import schedules_store as store_module

    # tests/api/conftest.py installs a SESSION-scoped data root, so `tmp_path`
    # is not unique per test here and rows would accumulate across tests. Give
    # each test its own subdirectory so persistence assertions are meaningful.
    per_test = tmp_path / uuid.uuid4().hex
    per_test.mkdir(parents=True, exist_ok=True)
    # subagent_schedules_db_path() resolves under DataPaths.base (the
    # project-level data_path), NOT data_root, so both must be pinned or the
    # session-scoped API data root wins.
    db_path = DataPaths(
        data_path=str(per_test), data_root=str(per_test), user_id="alice"
    ).subagent_schedules_db_path()

    def make_store(_path=None):
        return store_module.SubagentScheduleStore(db_path)

    def fake_paths(*_a, **_k):
        return DataPaths(data_path=str(per_test), data_root=str(per_test), user_id="alice")

    # create_schedule() builds its own SubagentScheduleStore() from
    # get_paths(), while the read/delete routes use the router's _store(). Patch
    # the store module's resolver so BOTH land in this test's directory —
    # patching only the router's factory let create and delete use different
    # databases.
    monkeypatch.setattr(store_module, "get_paths", fake_paths)
    monkeypatch.setattr(router_module, "SubagentScheduleStore", make_store)
    monkeypatch.setattr(router_module, "_store", lambda: make_store(), raising=False)
    yield db_path


def _schedules_via_api(client, user_id="alice"):
    """Read schedules back through the API.

    Deliberately not a direct SQLite read: the store's aiosqlite connection
    belongs to the TestClient's event loop, so `asyncio.run()` in a sync test
    reads across loops and reports phantom rows. The API is also the behaviour
    worth asserting.
    """
    r = client.get("/subagents/schedules", params={"user_id": user_id})
    assert r.status_code == 200, r.text
    return r.json()["schedules"]


@pytest.fixture(autouse=True)
def pinned_permissions(monkeypatch):
    """Pin capability permissions so these tests never read deployment config.

    `build_launch_plan` resolves each declared tool through the governance
    policy, which comes from the ambient config.yaml. A deployment that sets an
    admin `shell_execute: allow` therefore turns a "not ready" plan into a ready
    one, and these tests would fail or — worse — pass for the wrong reason.
    Same class of coupling as a governance-invariant test reading a mutable
    deployment config, so the permission is fixed here instead.
    """
    import src.sdk.subagent_capabilities as caps

    def _resolve(user_id, tool_name, tool_input):
        return "ask" if tool_name == "shell_execute" else "allow"

    monkeypatch.setattr(caps, "resolve_permission", _resolve)


@pytest.fixture
def ready_coordinator(monkeypatch):
    """A coordinator whose preflight yields a ready plan with one tool."""
    from agentprofile.models import AgentProfile

    from src.sdk.subagent_capabilities import ToolSelectionMode, build_launch_plan

    def make_plan(user_id="alice", workspace_id="personal"):
        plan = build_launch_plan(
            AgentProfile(name="researcher", tools=["files_read"]),
            user_id,
            workspace_id,
            ToolSelectionMode.ALLOWLIST,
        )
        return plan

    class _Coord:
        def __init__(self, user_id="alice", workspace_id="personal"):
            self.user_id = user_id
            self.workspace_id = workspace_id

        def preflight(self, name):
            return make_plan(self.user_id, self.workspace_id)

        def load_def(self, name):
            from agentprofile.models import AgentProfile

            return AgentProfile(name=name, tools=["files_read"])

    monkeypatch.setattr(
        "src.subagent.schedule_service._get_coordinator", lambda *_a, **_k: _Coord()
    )
    return make_plan


def _body(**over):
    body = {
        "subagent_name": "researcher",
        "task": "check the deployment",
        "run_at": RUN_AT,
    }
    body.update(over)
    return body


# --------------------------------------------------------------------------
# feature flag
# --------------------------------------------------------------------------


def test_create_is_rejected_while_the_feature_is_disabled(client, ready_coordinator):
    r = client.post("/subagents/schedules", json=_body(), params={"user_id": "alice"})
    assert r.status_code in (403, 404, 501, 400)
    assert "scheduling" in r.text.lower() or "not enabled" in r.text.lower()


# --------------------------------------------------------------------------
# trigger validation — all before persistence
# --------------------------------------------------------------------------


def _enable(monkeypatch):

    from src.config import get_settings

    settings = get_settings()
    object.__setattr__(
        settings.scheduling, "subagent_enabled", True
    )
    return settings


def test_create_one_shot_succeeds(client, ready_coordinator, monkeypatch):
    _enable(monkeypatch)
    r = client.post("/subagents/schedules", json=_body(), params={"user_id": "alice"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "scheduled"
    assert data["schedule_id"]
    assert data["manifest_hash"]


def test_create_recurring_succeeds(client, ready_coordinator, monkeypatch):
    _enable(monkeypatch)
    r = client.post(
        "/subagents/schedules",
        json=_body(run_at=None, cron="30 7 * * 1-5", timezone="Europe/Berlin"),
        params={"user_id": "alice"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "scheduled"


def test_both_triggers_is_rejected(client, ready_coordinator, monkeypatch):
    _enable(monkeypatch)
    r = client.post(
        "/subagents/schedules", json=_body(cron="0 8 * * *"), params={"user_id": "alice"}
    )
    assert r.status_code == 400
    assert "exactly one" in r.text.lower()


def test_neither_trigger_is_rejected(client, ready_coordinator, monkeypatch):
    _enable(monkeypatch)
    r = client.post(
        "/subagents/schedules", json=_body(run_at=None), params={"user_id": "alice"}
    )
    assert r.status_code == 400


def test_invalid_cron_is_rejected_before_persistence(client, ready_coordinator, monkeypatch):
    _enable(monkeypatch)
    r = client.post(
        "/subagents/schedules",
        json=_body(run_at=None, cron="not a cron @@"),
        params={"user_id": "alice"},
    )
    assert r.status_code == 400
    assert "cron" in r.text.lower()
    assert _schedules_via_api(client) == [], "nothing may be written on a rejected create"


def test_unknown_timezone_is_rejected_before_persistence(client, ready_coordinator, monkeypatch):
    _enable(monkeypatch)
    r = client.post(
        "/subagents/schedules",
        json=_body(run_at=None, cron="0 8 * * *", timezone="Nowhere/Bad"),
        params={"user_id": "alice"},
    )
    assert r.status_code == 400
    assert "timezone" in r.text.lower()


# --------------------------------------------------------------------------
# the frozen-manifest gate
# --------------------------------------------------------------------------


def test_a_not_ready_plan_is_rejected_and_nothing_is_written(client, monkeypatch):
    """The core governance property: no schedule without a launchable plan."""
    from agentprofile.models import AgentProfile

    from src.sdk.subagent_capabilities import ToolSelectionMode, build_launch_plan
    _enable(monkeypatch)

    def not_ready(user_id, workspace_id="personal"):
        # shell_execute defaults to ask -> the plan is not ready.
        return build_launch_plan(
            AgentProfile(name="researcher", tools=["shell_execute"]),
            user_id,
            workspace_id,
            ToolSelectionMode.ALLOWLIST,
        )

    class _Coord:
        def preflight(self, name):
            return not_ready("alice")

        def load_def(self, name):
            return AgentProfile(name=name, tools=["shell_execute"])

    monkeypatch.setattr(
        "src.subagent.schedule_service._get_coordinator", lambda *_a, **_k: _Coord()
    )

    r = client.post("/subagents/schedules", json=_body(), params={"user_id": "alice"})
    assert r.status_code == 400
    assert "allow" in r.text.lower() or "permission" in r.text.lower()
    assert _schedules_via_api(client) == [], "a rejected plan must leave no schedule"


def test_a_rejection_names_the_blocking_capability(client, monkeypatch):
    from agentprofile.models import AgentProfile

    from src.sdk.subagent_capabilities import ToolSelectionMode, build_launch_plan

    _enable(monkeypatch)

    class _Coord:
        def preflight(self, name):
            return build_launch_plan(
                AgentProfile(name="researcher", tools=["shell_execute"]),
                "alice",
                "personal",
                ToolSelectionMode.ALLOWLIST,
            )

        def load_def(self, name):
            return AgentProfile(name=name, tools=["shell_execute"])

    monkeypatch.setattr(
        "src.subagent.schedule_service._get_coordinator", lambda *_a, **_k: _Coord()
    )
    r = client.post("/subagents/schedules", json=_body(), params={"user_id": "alice"})
    assert r.status_code == 400
    assert "shell_execute" in r.text


def test_a_missing_subagent_is_rejected(client, monkeypatch):
    _enable(monkeypatch)

    class _Coord:
        def preflight(self, name):
            raise ValueError("Subagent 'ghost' not found.")

        def load_def(self, name):
            return None

    monkeypatch.setattr(
        "src.subagent.schedule_service._get_coordinator", lambda *_a, **_k: _Coord()
    )
    r = client.post(
        "/subagents/schedules", json=_body(subagent_name="ghost"), params={"user_id": "alice"}
    )
    assert r.status_code == 400
    assert "not found" in r.text.lower()


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


def test_list_and_get_round_trip(client, ready_coordinator, monkeypatch):
    _enable(monkeypatch)
    created = client.post("/subagents/schedules", json=_body(), params={"user_id": "alice"}).json()

    listed = client.get("/subagents/schedules", params={"user_id": "alice"})
    assert listed.status_code == 200
    assert any(s["schedule_id"] == created["schedule_id"] for s in listed.json()["schedules"])

    got = client.get(
        f"/subagents/schedules/{created['schedule_id']}", params={"user_id": "alice"}
    )
    assert got.status_code == 200
    assert got.json()["task"] == "check the deployment"


def test_runs_endpoint_returns_the_joined_run_state(client, ready_coordinator, monkeypatch):
    _enable(monkeypatch)
    created = client.post("/subagents/schedules", json=_body(), params={"user_id": "alice"}).json()
    r = client.get(
        f"/subagents/schedules/{created['schedule_id']}/runs", params={"user_id": "alice"}
    )
    assert r.status_code == 200
    assert "runs" in r.json()


# --------------------------------------------------------------------------
# user isolation
# --------------------------------------------------------------------------


def test_cross_user_get_is_404_not_403(client, ready_coordinator, monkeypatch):
    _enable(monkeypatch)
    created = client.post("/subagents/schedules", json=_body(), params={"user_id": "alice"}).json()
    r = client.get(
        f"/subagents/schedules/{created['schedule_id']}", params={"user_id": "bob"}
    )
    assert r.status_code == 404, "must not leak existence across users"


def test_cross_user_list_is_empty(client, ready_coordinator, monkeypatch):
    _enable(monkeypatch)
    client.post("/subagents/schedules", json=_body(), params={"user_id": "alice"})
    r = client.get("/subagents/schedules", params={"user_id": "bob"})
    assert r.status_code == 200
    assert r.json()["schedules"] == []


def test_cross_user_cancel_is_404(client, ready_coordinator, monkeypatch):
    _enable(monkeypatch)
    created = client.post("/subagents/schedules", json=_body(), params={"user_id": "alice"}).json()
    r = client.delete(
        f"/subagents/schedules/{created['schedule_id']}", params={"user_id": "bob"}
    )
    assert r.status_code == 404
    still = client.get(
        f"/subagents/schedules/{created['schedule_id']}", params={"user_id": "alice"}
    )
    assert still.json()["status"] == "scheduled", "bob must not have cancelled alice's schedule"


# --------------------------------------------------------------------------
# cancellation
# --------------------------------------------------------------------------


def test_delete_cancels_and_is_idempotent(client, ready_coordinator, monkeypatch):
    _enable(monkeypatch)
    created = client.post("/subagents/schedules", json=_body(), params={"user_id": "alice"}).json()
    url = f"/subagents/schedules/{created['schedule_id']}"

    first = client.delete(url, params={"user_id": "alice"})
    assert first.status_code == 200
    second = client.delete(url, params={"user_id": "alice"})
    assert second.status_code == 200, "cancel must be idempotent"

    got = client.get(url, params={"user_id": "alice"})
    assert got.json()["status"] == "cancelled"
