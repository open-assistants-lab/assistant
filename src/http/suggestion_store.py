"""In-memory suggestion store for tracking file edit suggestions."""

import time
from typing import Any

_suggestion_store: dict[str, dict[str, Any]] = {}
_MAX_SUGGESTIONS = 1000


def store_suggestion(suggestion_id: str, content: str, file_path: str, from_: int = -1, to: int = -1) -> None:
    """Store a suggestion for later retrieval."""
    if len(_suggestion_store) >= _MAX_SUGGESTIONS:
        oldest_id = min(_suggestion_store, key=lambda k: _suggestion_store[k]["created_at"])
        del _suggestion_store[oldest_id]
    _suggestion_store[suggestion_id] = {
        "content": content,
        "file_path": file_path,
        "from": from_,
        "to": to,
        "created_at": time.time(),
    }


def get_suggestion(suggestion_id: str) -> dict[str, Any] | None:
    """Retrieve a stored suggestion by ID."""
    return _suggestion_store.get(suggestion_id)


def get_all_suggestions_for_file(file_path: str) -> list[dict[str, Any]]:
    """Get all pending suggestions for a given file path."""
    result: list[dict[str, Any]] = []
    for sug_id, sug in _suggestion_store.items():
        if sug["file_path"] == file_path:
            result.append({"suggestion_id": sug_id, **sug})
    return result


def remove_suggestion(suggestion_id: str) -> None:
    """Remove a suggestion from the store."""
    _suggestion_store.pop(suggestion_id, None)


def clear() -> None:
    """Clear all stored suggestions."""
    _suggestion_store.clear()
