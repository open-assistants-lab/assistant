"""Phase-0 integration gate (roadmap P0-T8).

Four gate checks against the REAL FastAPI app + real store wiring:

  1. shared-secret auth      — API_KEY set; Bearer -> 200, absent -> 401
  2. stream a response       — POST /v1/message/stream yields SSE events
  3. audit export            — a real tool-call action lands in GET /v1/audit
  4. PROFILE.md bootstrap    — a user-level PROFILE.md drives create_sdk_loop
                              (persona applied => profile preferred)

Design choice: FastAPI TestClient (the project-wide convention, see
tests/api/conftest.py) — it exercises the real middleware, /v1 aliases, and
per-user stores without port management, and it is reliable in CI. A live
server is intentionally NOT spawned here because the same checks are covered
by `scripts/phase0_gate.sh` (curl against `uv run assistant http`).

Opt-in: tests are marked `phase0` and the suite's default `addopts` excludes
them (`-m "not phase0"`). Run with `-m phase0` explicitly.
"""

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.phase0]


@pytest.fixture
def client(_isolated_paths):
    """TestClient over the real app (data root isolated per test)."""
    from src.http.main import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_gate_shared_secret_auth(client, monkeypatch):
    """API_KEY set + SOLO_BYPASS=false: Bearer passes, absent token is 401."""
    from src.config import reload_settings

    monkeypatch.setenv("API_KEY", "gate-secret")
    monkeypatch.setenv("SOLO_BYPASS", "false")
    reload_settings()
    try:
        # No token -> 401.
        r_no = client.get("/v1/conversation", params={"user_id": "default_user"})
        assert r_no.status_code == 401, r_no.text

        # Valid Bearer -> reaches the router (200).
        r_ok = client.get(
            "/v1/conversation",
            params={"user_id": "default_user"},
            headers={"Authorization": "Bearer gate-secret"},
        )
        assert r_ok.status_code == 200, r_ok.text
    finally:
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.delenv("SOLO_BYPASS", raising=False)
        reload_settings()


# ---------------------------------------------------------------------------
# Stream gate
# ---------------------------------------------------------------------------


def test_gate_streams_response_via_v1(client, monkeypatch):
    """POST /v1/message/stream returns SSE text events (agent path stubbed)."""
    from src.http.routers import conversation as conversation_router
    from src.sdk.messages import StreamChunk

    async def _astub(value):
        return value

    class _FakeConv:
        def get_messages_by_session_id(self, session_id, limit=50):
            return []

        def get_messages_with_summary(self, session_id, limit=50):
            return []

        def add_message(self, *a, **kw):
            return None

        def persist_run(self, **kw):
            return None

    async def fake_run_sdk_agent_stream(**kwargs):
        yield StreamChunk.text_start()
        yield StreamChunk.text_delta(content="gate-ok")
        yield StreamChunk.text_end()
        yield StreamChunk.done(content="gate-ok")

    monkeypatch.setattr(
        conversation_router, "aget_message_store", lambda *a, **kw: _astub(_FakeConv())
    )
    monkeypatch.setattr(
        conversation_router, "run_sdk_agent_stream", fake_run_sdk_agent_stream
    )

    with client.stream(
        "POST",
        "/v1/message/stream",
        json={"message": "ping", "user_id": "default_user", "session_id": "gate-1"},
    ) as resp:
        assert resp.status_code == 200, resp.text
        body = "".join(resp.iter_text())
    # Real SSE frames must be present and the run must reach `done`.
    assert body.count("data:") >= 1, body
    assert '"type": "done"' in body, body


# ---------------------------------------------------------------------------
# Audit gate
# ---------------------------------------------------------------------------


def test_gate_audit_export_after_tool_action(client, monkeypatch):
    """A real tool-call action through the loop lands in GET /v1/audit."""
    import asyncio

    from src.sdk.audit import ensure_audit_store_subscribed
    from src.sdk.loop import AgentLoop, RunConfig
    from src.sdk.messages import Message
    from src.sdk.tools_core.time import time_get
    from tests.integration.fake_provider import FakeProvider

    user_id = "default_user"
    # Wire the production per-user audit store to the capture bus.
    ensure_audit_store_subscribed(user_id)

    provider = FakeProvider(
        responses=[
            {
                "tool_calls": [
                    {
                        "id": "call_gate_1",
                        "name": "time_get",
                        "arguments": {"timezone": "UTC"},
                    }
                ]
            },
            {"content": "done"},
        ]
    )
    loop = AgentLoop(
        provider=provider,
        tools=[time_get],
        system_prompt="gate",
        user_id=user_id,
        run_config=RunConfig(max_llm_calls=2),
    )

    async def _run():
        await loop.run([Message.user("what time is it?")])

    asyncio.run(_run())

    r = client.get("/v1/audit", params={"user_id": user_id})
    assert r.status_code == 200, r.text
    assert int(r.headers.get("X-Audit-Count", "0")) >= 1, r.text
    assert "tool_call" in r.text, r.text


# ---------------------------------------------------------------------------
# PROFILE.md bootstrap gate
# ---------------------------------------------------------------------------


def test_gate_profile_bootstrap_drives_loop(_isolated_paths, monkeypatch, tmp_path):
    """A user-level PROFILE.md instantiates the main loop with its persona."""
    import asyncio
    from types import SimpleNamespace

    from agentprofile import AgentProfile, dumps_profile

    from src.sdk import runner as runner_mod

    # Write PROFILE.md at the user root (data_root for default_user).
    user_dir = tmp_path
    profile = AgentProfile(
        name="gate-agent",
        description="gate",
        model="ollama:minimax-m2.5",
        system_prompt="You are the GATE AGENT persona.",
    )
    (user_dir / "PROFILE.md").write_text(dumps_profile(profile), encoding="utf-8")

    # Patch the runner harness so no real model/tools/skills are needed.
    settings = SimpleNamespace(
        memory=SimpleNamespace(
            summarization=SimpleNamespace(
                enabled=False,
                model=None,
                prompt_file=None,
                trim_tokens_to_summarize=4000,
                get_trigger=lambda: ("messages", 2),
                get_keep=lambda: ("messages", 1),
            )
        ),
        verification=SimpleNamespace(enabled=False),
        langfuse=SimpleNamespace(enabled=False, public_key="", secret_key="", host=""),
        agent=SimpleNamespace(model="ollama:minimax-m2.5"),
    )
    monkeypatch.setattr(runner_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(runner_mod, "get_native_tools", lambda: [])
    monkeypatch.setattr(runner_mod, "_seed_default_workspace", lambda: None)
    monkeypatch.setattr(runner_mod, "_get_system_prompt", lambda *a, **kw: "BASE")
    monkeypatch.setattr(
        "src.config.user_settings_service.load_saved_user_settings", lambda user_id: None
    )

    class FakeIndex:
        def count(self):
            return 0

        def clear(self):
            pass

        def index_tool(self, *a, **kw):
            pass

    monkeypatch.setattr(
        "src.sdk.tool_index.get_or_create_index",
        lambda *a, **kw: (FakeIndex(), lambda: None),
    )

    class FakeProvider:
        provider_id = "ollama"
        model = "minimax-m2.5"

    monkeypatch.setattr(
        runner_mod, "get_cached_model_provider", lambda *a, **kw: FakeProvider()
    )

    async def _create():
        return await runner_mod.create_sdk_loop("default_user")

    loop = asyncio.run(_create())
    assert loop is not None
    # The profile persona must have been applied to the loop's system prompt.
    assert "GATE AGENT" in (loop.system_prompt or ""), loop.system_prompt
