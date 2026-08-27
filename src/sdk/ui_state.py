"""In-memory UI state tracking per user."""
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

from src.storage.paths import DEFAULT_USER_ID


class UiState:
    """Per-user UI state with ring buffer of recent events."""

    def __init__(self, max_events: int = 100) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._canvas_html: str | None = None
        self._current_tab: str = "canvas"

    def track_event(self, event: dict[str, Any]) -> None:
        self._events.append(event)

    def set_canvas_html(self, html: str) -> None:
        self._canvas_html = html

    def set_current_tab(self, tab: str) -> None:
        self._current_tab = tab

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_tab": self._current_tab,
            "events": list(self._events),
            "canvas_html": self._canvas_html,
        }

    def to_markdown(self) -> str:
        lines = [f"**Current tab:** {self._current_tab}"]
        if self._events:
            lines.append(f"**Recent events ({len(self._events)}):**")
            for e in self._events:
                lines.append(f"- {e.get('type', 'unknown')} on {e.get('target', '?')}")
        if self._canvas_html:
            lines.append("**Canvas HTML:** present")
        return "\n".join(lines)


_store: dict[str, UiState] = {}
_lock = threading.Lock()


def get_state(user_id: str =  DEFAULT_USER_ID) -> UiState:
    with _lock:
        if user_id not in _store:
            _store[user_id] = UiState()
        return _store[user_id]


def track_event(user_id: str, event: dict[str, Any]) -> None:
    state = get_state(user_id)
    event.setdefault("timestamp", datetime.now(UTC).isoformat())
    state.track_event(event)


def set_canvas_html(user_id: str, html: str) -> None:
    state = get_state(user_id)
    state.set_canvas_html(html)
