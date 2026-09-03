"""P1-T12: model-visible ⟺ logged invariant tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

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


def _emit(se, seq, session_id, type_, data, run_id="r1"):
    store = se.get_session_event_store("u1")
    store.append(
        parse_run_event(
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
    )


class TestInvariant:
    def test_invariant_green_on_logged_turn(self, slog):
        from src.sdk.session_invariant import assert_model_visible_logged

        _emit(slog, 1, "s1", "user_prompt", {"content": "Plan the migration"})
        _emit(slog, 2, "s1", "text_delta", {"block_id": "t1", "delta": "Starting."})
        _emit(
            slog,
            3,
            "s1",
            "tool_input_end",
            {"block_id": "c1", "tool_call_id": "c1", "arguments": {"q": "x"}},
        )
        _emit(
            slog,
            4,
            "s1",
            "tool_result",
            {
                "block_id": "c1",
                "tool_call_id": "c1",
                "name": "search",
                "status": "completed",
                "content": "3 hits",
            },
        )
        _emit(slog, 5, "s1", "system_prompt", {"content": "You are helpful."})

        messages = [
            Message.user("Plan the migration"),
            Message.assistant(
                "Starting.",
                tool_calls=[{"id": "c1", "name": "search", "arguments": {"q": "x"}}],
            ),
            Message.tool_result(tool_call_id="c1", content="3 hits", name="search"),
        ]
        assert_model_visible_logged("s1", "u1", messages, "You are helpful.")

    def test_deliberately_unlogged_input_fails(self, slog):
        from src.sdk.session_invariant import (
            SessionInvariantError,
            assert_model_visible_logged,
        )

        _emit(slog, 1, "s1", "user_prompt", {"content": "logged prompt"})
        with pytest.raises(SessionInvariantError, match="not logged"):
            assert_model_visible_logged(
                "s1",
                "u1",
                [
                    Message.user("logged prompt"),
                    Message.user("UNLOGGED input"),
                ],
            )

    def test_missing_header_fails(self, slog):
        from src.sdk.session_invariant import (
            SessionInvariantError,
            assert_model_visible_logged,
        )

        _emit(slog, 1, "s1", "user_prompt", {"content": "Plan"})
        with pytest.raises(SessionInvariantError, match="folded header"):
            assert_model_visible_logged("s1", "u1", [Message.user("Plan")], "You are helpful.")

    def test_diverging_header_fails(self, slog):
        from src.sdk.session_invariant import (
            SessionInvariantError,
            assert_model_visible_logged,
        )

        _emit(slog, 1, "s1", "user_prompt", {"content": "Plan"})
        _emit(slog, 2, "s1", "system_prompt", {"content": "Different header."})
        with pytest.raises(SessionInvariantError, match="diverges"):
            assert_model_visible_logged("s1", "u1", [Message.user("Plan")], "You are helpful.")
