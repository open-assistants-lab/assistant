"""Session-event log + history projection (R-SL1, P1-T10).

The log is the append-only per-user store of canonical RunEvents
(src/sdk/run_events.py — the P0-T9 session-event schema). Emission is
opt-in via the ``session_log.enabled`` settings flag; disabled (the
shipped default) every helper here is a no-op and history reconstruction
falls back to the existing MessageStore path.

``deriveMessages`` projects the model-visible conversation from the log:
the same message list the model saw, rebuildable without touching the
per-step MessageStore serialization.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from typing import Any

from src.sdk.messages import Message
from src.sdk.run_events import (
    InjectionEvent,
    ReasoningDeltaEvent,
    RunEvent,
    SystemPromptEvent,
    TextDeltaEvent,
    ToolInputEndEvent,
    ToolResultEvent,
    UserPromptEvent,
    parse_run_event,
)


def session_log_enabled() -> bool:
    cfg = getattr(_settings(), "session_log", None)
    return bool(cfg is not None and cfg.enabled)


def _settings() -> Any:  # pragma: no cover - thin indirection for tests
    from src.config.settings import get_settings

    return get_settings()


class SessionEventStore:
    """Append-only per-user SQLite store of canonical RunEvents.

    Write-only append keyed by (session_id, sequence); read via
    ``events(session_id)`` ordered by sequence. No update/delete surface.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        from pathlib import Path

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_events (
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_json TEXT NOT NULL,
                PRIMARY KEY (session_id, sequence)
            )
            """
        )
        self._conn.commit()

    def append(self, event: RunEvent) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO session_events (session_id, sequence, event_json) VALUES (?, ?, ?)",
                (event.session_id, event.sequence, event.model_dump_json()),
            )

    def next_sequence(self, session_id: str) -> int:
        """Next free sequence for this session (per-session monotonic)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM session_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row[0]) + 1

    def events(self, session_id: str) -> list[RunEvent]:
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                "SELECT event_json FROM session_events WHERE session_id = ? ORDER BY sequence ASC",
                (session_id,),
            ).fetchall()
        return [parse_run_event(json.loads(r["event_json"])) for r in rows]


_session_lock = threading.Lock()
_session_stores: dict[str, SessionEventStore] = {}


def reset_session_stores() -> None:
    """Test hygiene: drop cached per-user stores (paths are monkeypatched)."""
    with _session_lock:
        _session_stores.clear()


def get_session_event_store(user_id: str) -> SessionEventStore:
    from src.storage.paths import get_paths

    paths = get_paths(user_id=user_id, workspace_id="personal")
    db = str(paths.root / "session_log.db")
    with _session_lock:
        store = _session_stores.get(user_id)
        if store is None or store._db_path != db:
            store = SessionEventStore(db)
            _session_stores[user_id] = store
        return store


def log_event(user_id: str, event: RunEvent) -> None:
    """Append one event to the user's session log. No-op when disabled."""
    if not session_log_enabled():
        return
    get_session_event_store(user_id).append(event)


# ---------------------------------------------------------------------------
# History projection (P1-T10)
# ---------------------------------------------------------------------------


def _message_text(msg: Message) -> str:
    return msg.content if isinstance(msg.content, str) else ""


def deriveMessages(session_id: str, user_id: str) -> list[Message]:  # noqa: N802 - plan-named contract (R-SL1)
    """Project the model-visible conversation from the session-event log."""
    events = get_session_event_store(user_id).events(session_id)
    return _project(events)


def derive_system_prompt(session_id: str, user_id: str) -> str | None:
    """The assembled system prompt (folded header) as last logged."""
    prompt: str | None = None
    for ev in get_session_event_store(user_id).events(session_id):
        if isinstance(ev, SystemPromptEvent):
            prompt = ev.data.content
    return prompt


def _project(events: list[RunEvent]) -> list[Message]:
    messages: list[Message] = []
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    pending_tools: list[dict[str, Any]] = []

    def flush_assistant() -> None:
        content = "".join(text_parts)
        reasoning = "".join(reasoning_parts) or None
        if content or pending_tools or reasoning:
            from src.sdk.messages import ToolCall

            candidate = Message.assistant(
                content=content,
                tool_calls=[ToolCall(**pt) for pt in pending_tools] or None,
                reasoning=reasoning,
            )
            # Merge an empty continuation assistant (tool calls emitted after
            # a result, no new text) into the previous assistant — the model
            # saw one turn; the projection must too.
            if not content and not reasoning and candidate.tool_calls:
                # Merge into the most recent assistant even across interleaved
                # tool results (tool msgs sit between assistant turns).
                target = next(
                    (m for m in reversed(messages) if m.role == "assistant"),
                    None,
                )
                if target is not None:
                    target.tool_calls = list(target.tool_calls or []) + list(
                        candidate.tool_calls or []
                    )
                else:
                    messages.append(candidate)
            else:
                messages.append(candidate)
        text_parts.clear()
        reasoning_parts.clear()
        pending_tools.clear()

    tool_names: dict[str, str] = {}
    for ev in events:
        if isinstance(ev, ToolResultEvent):
            tool_names[ev.data.tool_call_id] = ev.data.name
    for ev in events:
        if isinstance(ev, UserPromptEvent):
            flush_assistant()
            messages.append(Message.user(ev.data.content))
        elif isinstance(ev, (SystemPromptEvent, InjectionEvent)):
            flush_assistant()
        elif ev.type == "text_delta":
            text_parts.append(ev.data.delta)
        elif ev.type == "text_end":
            flush_assistant()
        elif ev.type == "reasoning_delta":
            reasoning_parts.append(ev.data.delta)
        elif isinstance(ev, ToolInputEndEvent):
            pending_tools.append(
                {
                    "id": ev.data.tool_call_id,
                    "name": tool_names.get(ev.data.tool_call_id, ev.data.tool_call_id),
                    "arguments": dict(ev.data.arguments),
                }
            )
        elif isinstance(ev, ToolResultEvent):
            flush_assistant()
            # ToolEndData carries no name; the paired ToolResultEvent does —
            # retroactively name the pending tool call (same call_id).
            for pt in pending_tools:
                if pt["id"] == ev.data.tool_call_id:
                    pt["name"] = ev.data.name
            messages.append(
                Message.tool_result(
                    tool_call_id=ev.data.tool_call_id,
                    content=str(ev.data.content),
                    name=ev.data.name,
                )
            )
    flush_assistant()
    return messages


def log_model_message(
    user_id: str,
    session_id: str,
    run_id: str,
    sequence: int,
    message: Message,
) -> int:
    """Log one model-visible Message as canonical events. Returns the next
    sequence value. No-op (same sequence) when disabled."""
    if not session_log_enabled():
        return sequence

    nonlocal_sequence: dict[str, int] = {"next": 0}  # 0 = unallocated

    def _emit(event_cls: Any, data: Any) -> None:
        if nonlocal_sequence["next"] == 0:
            nonlocal_sequence["next"] = get_session_event_store(
                user_id
            ).next_sequence(session_id)
        ev = parse_run_event(
            {
                "schema_version": 1,
                "event_id": uuid.uuid4().hex,
                "sequence": nonlocal_sequence["next"],
                "timestamp": _utcnow(),
                "session_id": session_id,
                "run_id": run_id,
                "attempt": 1,
                "type": event_cls.model_fields["type"].default,
                "data": data,
            }
        )
        nonlocal_sequence["next"] += 1
        log_event(user_id, ev)

    content = _message_text(message)
    if message.role == "user" and content:
        _emit(UserPromptEvent, {"content": content})
    elif message.role == "assistant":
        if message.reasoning:
            _emit(
                ReasoningDeltaEvent,
                {"block_id": "reasoning", "delta": message.reasoning},
            )
        for tc in message.tool_calls or []:
            args = tc.arguments if isinstance(tc.arguments, dict) else {}
            _emit(
                ToolInputEndEvent,
                {
                    "block_id": tc.id,
                    "tool_call_id": tc.id,
                    "arguments": args,
                },
            )
        if content:
            _emit(TextDeltaEvent, {"block_id": "text", "delta": content})
    elif message.role == "tool":
        _emit(
            ToolResultEvent,
            {
                "block_id": message.tool_call_id or "",
                "tool_call_id": message.tool_call_id or "",
                "name": message.name or "",
                "status": "completed",
                "content": content,
            },
        )
    elif message.role == "system":
        _emit(InjectionEvent, {"kind": "supervisor", "content": content})
    return sequence + 1


def _utcnow() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)
