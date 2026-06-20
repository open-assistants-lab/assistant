"""In-memory suggestion store for tracking file edit suggestions."""

import time
from typing import Any

_suggestion_store: dict[str, dict[str, Any]] = {}


def store_suggestion(suggestion_id: str, content: str, file_path: str, from_: int = -1, to: int = -1) -> None:
    """Store a suggestion for later retrieval."""
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


def remove_suggestion(suggestion_id: str) -> None:
    """Remove a suggestion from the store."""
    _suggestion_store.pop(suggestion_id, None)


def clear() -> None:
    """Clear all stored suggestions."""
    _suggestion_store.clear()
