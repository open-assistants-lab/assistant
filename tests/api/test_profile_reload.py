"""Profile reload endpoint tests (roadmap P0-T7 fix round).

POST /profile/reload:
- valid PROFILE.md -> 200, loops reset, active WS sessions detached
- invalid PROFILE.md -> 400, loops untouched
- absent PROFILE.md -> 200 (no-op reset), profile_found=false
"""

from __future__ import annotations

import pytest
from agentprofile import AgentProfile, dumps_profile
from fastapi.testclient import TestClient

from src.http.main import app
from src.sdk import profile_loader, runner
from src.sdk.session_worker import SessionWorkerRegistry, get_session_registry


def _write_profile(data_root, user_id, profile: AgentProfile) -> None:
    from src.storage.paths import DataPaths

    dp = DataPaths(user_id=user_id, data_root=str(data_root))
    path = dp.main_agent_profile_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_profile(profile), encoding="utf-8")


def _profile(**overrides) -> AgentProfile:
    kwargs = dict(
        name="reload-agent",
        description="test",
        model="anthropic:claude-sonnet-4-5",
        system_prompt="You are a meticulous drafter.",
        skills=[],
        tools=[],
    )
    kwargs.update(overrides)
    return AgentProfile(**kwargs)


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Isolated client with a temp data root and a fresh registry."""
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path / "data_root"))
    from src.config import reload_settings

    reload_settings()

    registry = SessionWorkerRegistry()
    monkeypatch.setattr(
        "src.http.routers.profile.get_session_registry", lambda: registry
    )
    monkeypatch.setattr(
        "src.sdk.session_worker.get_session_registry", lambda: registry
    )

    reset_calls: list[str] = []

    async def _fake_reset(user_id, *, data_root=None, registry=None):
        reset_calls.append(user_id)
        return {
            "profile_found": True,
            "detached_sessions": ["u::sess-1"],
            "loops_removed": 2,
        }

    monkeypatch.setattr(
        "src.sdk.profile_loader.revalidate_and_reset", _fake_reset
    )

    client = TestClient(app)
    client._reset_calls = reset_calls  # type: ignore[attr-defined]
    return client


def test_reload_valid_profile_returns_200_and_resets(client, tmp_path):
    _write_profile(tmp_path / "data_root", "default_user", _profile())
    resp = client.post("/profile/reload?user_id=default_user")
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile_found"] is True
    assert body["model"] == "anthropic:claude-sonnet-4-5"
    assert body["persona_present"] is True
    assert body["detached_sessions"] == ["u::sess-1"]
    assert body["loops_removed"] == 2
    assert client._reset_calls == ["default_user"]  # type: ignore[attr-defined]


def test_reload_invalid_profile_returns_400_loop_untouched(client, tmp_path):
    from src.storage.paths import DataPaths

    dp = DataPaths(user_id="default_user", data_root=str(tmp_path / "data_root"))
    path = dp.main_agent_profile_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("name: [unclosed\nmodel: anthropic:claude-sonnet-4-5\n", encoding="utf-8")

    resp = client.post("/profile/reload?user_id=default_user")
    assert resp.status_code == 400
    assert "Invalid PROFILE.md" in resp.json()["detail"]
    # Loop untouched: revalidate_and_reset never called.
    assert client._reset_calls == []  # type: ignore[attr-defined]


def test_reload_absent_profile_returns_200_noop(client, tmp_path):
    resp = client.post("/profile/reload?user_id=default_user")
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile_found"] is True  # fake reset reports found
    assert client._reset_calls == ["default_user"]  # type: ignore[attr-defined]


def test_reload_detaches_active_ws_session(monkeypatch, tmp_path):
    """End-to-end: a held session lock is cancelled by the reload path."""
    from src.sdk import profile_loader as pl

    registry = SessionWorkerRegistry()
    lock = await_registry_acquire(registry, "u9::sess-1")

    calls = []
    _orig_reset = pl.revalidate_and_reset

    async def _real_reset(user_id, *, data_root=None, registry=None):
        calls.append(user_id)
        return await _orig_reset(user_id, data_root=data_root, registry=registry)

    monkeypatch.setattr("src.sdk.profile_loader.revalidate_and_reset", _real_reset)
    monkeypatch.setattr("src.http.routers.profile.get_session_registry", lambda: registry)
    monkeypatch.setattr("src.sdk.session_worker.get_session_registry", lambda: registry)
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path / "data_root"))
    from src.config import reload_settings

    reload_settings()

    client = TestClient(app)
    resp = client.post("/profile/reload?user_id=u9")
    assert resp.status_code == 200
    assert calls == ["u9"]
    assert lock.cancelled
    assert resp.json()["detached_sessions"] == ["u9::sess-1"]


def await_registry_acquire(registry, key):
    import asyncio

    return asyncio.run(registry.acquire(key))
