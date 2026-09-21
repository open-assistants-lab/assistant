"""R-SL1 session-log tests: projection fidelity + emission wiring (P1-T10/T11)."""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime

import pytest

from src.sdk.compression import (
    CompressionReason,
    CompressionStatus,
    CompressionTelemetry,
)
from src.sdk.messages import Message
from src.sdk.run_events import parse_run_event


@pytest.fixture()
def slog(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod
    from src.config import settings as settings_module
    from src.sdk import session_events as se

    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "data"),
        raising=False,
    )
    monkeypatch.setattr(se, "_session_stores", {})
    monkeypatch.setattr(se, "session_log_enabled", lambda: True)
    settings_module._config = None
    yield se
    settings_module._config = None
    se.reset_session_stores()


def _env(seq, session_id, run_id, type_, data):
    return parse_run_event(
        {
            "schema_version": 1,
            "event_id": f"e{seq}",
            "sequence": seq,
            "timestamp": datetime.now(UTC),
            "session_id": session_id,
            "run_id": run_id,
            "attempt": 1,
            "type": type_,
            "data": data,
        }
    )


def _seed_multi_tool_turn(se, session_id="s1", run_id="r1"):
    store = se.get_session_event_store("u1")
    seq = 1

    def emit(type_, data):
        nonlocal seq
        store.append(_env(seq, session_id, run_id, type_, data))
        seq += 1

    emit("user_prompt", {"content": "Plan the migration"})
    emit("system_prompt", {"content": "You are helpful."})
    emit("text_delta", {"block_id": "t1", "delta": "Starting."})
    emit("reasoning_delta", {"block_id": "r1", "delta": "Think about order."})
    emit("tool_input_end", {"block_id": "b1", "tool_call_id": "c1", "arguments": {"q": "x"}})
    emit("tool_result", {"block_id": "b2", "tool_call_id": "c1", "name": "search", "status": "completed", "content": "3 hits"})
    emit("tool_input_end", {"block_id": "b3", "tool_call_id": "c2", "arguments": {"steps": 2}})
    emit("tool_result", {"block_id": "b4", "tool_call_id": "c2", "name": "plan", "status": "completed", "content": "planned"})
    emit("text_delta", {"block_id": "t2", "delta": "Done."})
    return store


class TestDerive:
    def test_project_matches_model_visible_history(self, slog):
        _seed_multi_tool_turn(slog)
        msgs = slog.deriveMessages("s1", "u1")
        assert msgs[0].role == "user"
        assert msgs[0].content == "Plan the migration"
        assistant = [m for m in msgs if m.role == "assistant"]
        assert len(assistant) == 2
        assert assistant[0].content == "Starting."
        assert [tc.name for tc in assistant[0].tool_calls or []] == ["search", "plan"]
        assert assistant[0].reasoning == "Think about order."
        assert assistant[1].content == "Done."
        tool_msgs = [m for m in msgs if m.role == "tool"]
        assert [m.name for m in tool_msgs] == ["search", "plan"]

    def test_multi_step_tool_turns_replay_correctly(self, slog):
        _seed_multi_tool_turn(slog)
        msgs = slog.deriveMessages("s1", "u1")
        assert [m.role for m in msgs] == ["user", "assistant", "tool", "tool", "assistant"]
        assert msgs[2].tool_call_id == "c1"
        assert msgs[2].content == "3 hits"

    def test_derive_system_prompt_returns_last_header(self, slog):
        _seed_multi_tool_turn(slog)
        assert slog.derive_system_prompt("s1", "u1") == "You are helpful."

    def test_disabled_log_yields_empty_projection(self, slog, monkeypatch):
        import src.sdk.session_events as se

        monkeypatch.setattr(se, "session_log_enabled", lambda: False)
        assert se.log_event("u1", _env(1, "s1", "r1", "user_prompt", {"content": "x"})) is None
        assert se.deriveMessages("s1", "u1") == []


class TestEmission:
    def test_loop_logs_user_header_and_messages(self, slog, monkeypatch):
        from src.sdk.loop import AgentLoop
        from src.sdk.messages import StreamChunk
        from src.sdk.tools import tool

        class Provider:
            def chat_stream(self, messages, tools=None, model=None, **kwargs):
                async def _stream():
                    yield StreamChunk.tool_input_start(tool="t1", call_id="c1")
                    yield StreamChunk.tool_input_delta(
                        call_id="c1", content=_json.dumps({"q": "x"})
                    )
                    yield StreamChunk.tool_input_end(call_id="c1", tool="t1")
                    yield StreamChunk.done(content="")

                return _stream()

        @tool(name="t1")
        async def t1(q: str = "") -> str:
            """Probe tool."""
            return "ok"

        import asyncio

        loop = AgentLoop(
            provider=Provider(),
            tools=[t1],
            user_id="u1",
            system_prompt="You are helpful.",
            run_config=None,
        )
        loop._flow_session_id = "s1"
        loop._flow_run_id = "r1"

        async def consume():
            chunks = []
            async for c in loop.run_stream([Message.user("Plan the migration")]):
                chunks.append(c)
            return chunks

        asyncio.run(consume())

        msgs = slog.deriveMessages("s1", "u1")
        roles = [m.role for m in msgs]
        print("DBGROLES:", roles)
        assert "user" in roles and "assistant" in roles and "tool" in roles
        assert slog.derive_system_prompt("s1", "u1") == "You are helpful."
        user = [m for m in msgs if m.role == "user"][0]
        assert user.content == "Plan the migration"

    def test_steer_delivery_logged_as_injection(self, slog):
        from src.sdk.loop import AgentLoop
        from src.sdk.state import AgentState

        loop = AgentLoop(provider=None, tools=[], user_id="u1", run_config=None)
        loop._flow_session_id = "s1"
        loop._flow_run_id = "r1"
        st = AgentState()
        st.message_observer = loop._log_session_message
        loop.steer("focus on the DB step")
        assert loop._drain_steer(st)

        events = slog.get_session_event_store("u1").events("s1")
        kinds = [e.data.kind for e in events if getattr(e, "type", "") == "injection"]
        assert "steer" in kinds


class TestCompressionLogging:
    """D2 task 5: successful compression becomes a durable replayable record."""

    @staticmethod
    def _snapshot(attempt: int, tokens: int):
        from src.sdk.run_models import (
            ContextFreshness,
            ContextSnapshot,
            ContextSource,
        )

        return ContextSnapshot(
            model="mock:test",
            attempt=attempt,
            llm_call_index=attempt,
            estimated_tokens=tokens,
            context_window=100_000,
            percentage=tokens / 100_000 * 100,
            source=ContextSource.PREPARED_CONTEXT,
            freshness=ContextFreshness.LIVE,
            estimated=True,
        )

    def _telemetry(self, status: CompressionStatus) -> CompressionTelemetry:
        kwargs: dict = {
            "status": status,
            "reason": CompressionReason.THRESHOLD,
            "summary_model": "mock:test",
            "persistence": {"status": "not_requested"},
            "before_context": self._snapshot(1, 46_000),
        }
        if status is CompressionStatus.SUCCEEDED:
            kwargs.update(
                before_message_count=12,
                after_message_count=2,
                summarized_message_count=10,
                after_context=self._snapshot(1, 9_000),
            )
        else:
            kwargs["error_code"] = "not_compressed"
        return CompressionTelemetry(**kwargs)

    def test_successful_compression_projects_a_timeline_notice(self, slog):
        se = slog
        assert (
            se.log_compression("u1", "s1", "r1", 1, self._telemetry(CompressionStatus.SUCCEEDED))
            == 2
        )
        messages = se.deriveMessages("s1", "u1")
        assert [m.content for m in messages] == ["Context updated · 46k → 9k tokens"]

    def test_failed_compression_writes_no_success_record(self, slog):
        se = slog
        assert (
            se.log_compression("u1", "s1", "r1", 1, self._telemetry(CompressionStatus.FAILED))
            == 1
        )
        assert se.deriveMessages("s1", "u1") == []

    def test_disabled_log_is_a_no_op(self, slog, monkeypatch):
        se = slog
        monkeypatch.setattr(se, "session_log_enabled", lambda: False)
        assert (
            se.log_compression("u1", "s1", "r1", 7, self._telemetry(CompressionStatus.SUCCEEDED))
            == 7
        )
        assert se.get_session_event_store("u1").events("s1") == []

    def test_stale_loop_cache_does_not_drop_the_record(self, slog):
        """D2 re-review N2: allocation comes from the store, not the cache.

        A second cached loop for the same session can hold a stale
        `_session_log_seq`; using it would collide on the PRIMARY KEY and the
        emit-only error handling would silently drop the compression record.
        """
        from src.sdk.loop import AgentLoop

        se = slog
        store = se.get_session_event_store("u1")
        store.append(_env(1, "s1", "r1", "user_prompt", {"content": "hi"}))

        loop = AgentLoop(provider=None, tools=[], user_id="u1")
        loop._flow_session_id = "s1"
        loop._flow_run_id = "r1"
        loop._session_log_seq = 1  # stale: row 1 already exists
        loop._log_session_compression(self._telemetry(CompressionStatus.SUCCEEDED))

        rows = store.events("s1")
        assert [e.type for e in rows] == ["user_prompt", "context_compressed"]
        assert rows[1].sequence == 2
