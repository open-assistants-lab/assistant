"""Turn pagination: runs must never be split across fetch-batch boundaries.

Audit P1: get_turns fetched limit*5 rows per batch and flushed the trailing
partial turn at every batch boundary, so a single run larger than the batch
window was emitted as two turns (and the cursor could land mid-run).
"""

from __future__ import annotations

import tempfile

from src.storage.messages import MessageStore


def _store() -> MessageStore:
    temp_dir = tempfile.TemporaryDirectory()
    store = MessageStore("test_user", base_dir=temp_dir.name)
    store._temp_dir = temp_dir
    return store


def _seed_run(store: MessageStore, run_id: str, n_steps: int, session_id: str = "a") -> None:
    """Seed one run with n messages (user + tools + assistant, same run_id)."""
    for i in range(n_steps):
        role = "user" if i == 0 else ("assistant" if i == n_steps - 1 else "tool")
        store.add_message(
            role, f"{run_id}-msg-{i}", metadata={"run_id": run_id}, session_id=session_id
        )


def test_run_spanning_batch_boundary_is_one_turn() -> None:
    """A run larger than limit*5 (the fetch window) is one turn, not two."""
    store = _store()
    # limit=2 -> fetch window = 10 rows; seed 12 rows in ONE run.
    _seed_run(store, "run-big", n_steps=12)
    turns, cursor = store.get_turns("a", limit=2)
    assert len(turns) == 1
    assert turns[0]["run_id"] == "run-big"
    assert len(turns[0]["messages"]) == 12
    assert cursor is None


def test_pagination_boundary_keeps_runs_intact_across_pages() -> None:
    """Cursor pages must not split a run even when a page boundary lands mid-run."""
    store = _store()
    # Page window = limit*5 = 10 rows. run-1 has 8 messages (fits page 1),
    # run-2 has 8 messages (spans pages 1-2), run-3 has 2 messages.
    _seed_run(store, "run-1", n_steps=8)
    _seed_run(store, "run-2", n_steps=11)
    _seed_run(store, "run-3", n_steps=2)

    turns, cursor = store.get_turns("a", limit=2)
    assert len(turns) == 2
    assert [t["run_id"] for t in turns] == ["run-1", "run-2"]
    assert len(turns[1]["messages"]) == 11  # run-2 not split
    assert cursor is not None

    turns2, cursor2 = store.get_turns("a", limit=2, cursor=cursor)
    assert len(turns2) == 1
    assert turns2[0]["run_id"] == "run-3"
    assert len(turns2[0]["messages"]) == 2
    assert cursor2 is None


def test_exact_fetch_size_multiple_flushes_trailing_run() -> None:
    """A session with exactly limit*5 rows must still return its turns.

    Regression: when the last batch is EXACTLY fetch_size rows, the loop
    advanced, the next fetch returned empty, and the `not rows_raw` break
    exited without flushing the still-open run -> get_turns returned []
    (or too few turns) silently.
    """
    store = _store()
    # limit=2 -> fetch window = 10 rows; seed exactly 10 rows in ONE run.
    _seed_run(store, "run-exact", n_steps=10)
    turns, cursor = store.get_turns("a", limit=2)
    assert len(turns) == 1
    assert turns[0]["run_id"] == "run-exact"
    assert len(turns[0]["messages"]) == 10
    assert cursor is None

    # 2 * limit * 5 = 20 rows in one run -> still one turn.
    store2 = _store()
    _seed_run(store2, "run-exact-2", n_steps=20)
    turns2, cursor2 = store2.get_turns("a", limit=2)
    assert len(turns2) == 1
    assert turns2[0]["run_id"] == "run-exact-2"
    assert len(turns2[0]["messages"]) == 20
    assert cursor2 is None


def test_small_pages_no_duplicate_runs() -> None:
    """Each run appears exactly once across many small pages."""
    store = _store()
    for i in range(6):
        _seed_run(store, f"run-{i}", n_steps=3)
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        turns, cursor = store.get_turns("a", limit=2, cursor=cursor)
        for t in turns:
            seen.append(t["run_id"])
        if cursor is None:
            break
    assert seen == [f"run-{i}" for i in range(6)]
