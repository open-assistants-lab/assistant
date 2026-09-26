"""Durable subagent schedule store contract (issue #46, Task 1)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from src.subagent.schedules_store import (
    SCHEDULE_STATUSES,
    TERMINAL_STATUSES,
    TRIGGER_KINDS,
    SubagentScheduleStore,
)

RUN_AT = datetime(2026, 9, 26, 18, 0, tzinfo=UTC)


def _manifest(tool: str = "files_read") -> dict[str, Any]:
    return {
        "version": 1,
        "user_id": "alice",
        "agent_name": "researcher",
        "effective_tools": [tool],
        "effective_skills": [],
        "canonical_manifest": {
            "version": 1,
            "effective_tools": [tool],
            "effective_skills": [],
        },
    }


def _store(tmp_path: Path) -> SubagentScheduleStore:
    return SubagentScheduleStore(tmp_path / "subagent_schedules.db")


async def _create_once(
    store: SubagentScheduleStore,
    *,
    user_id: str = "alice",
    subagent_name: str = "researcher",
    task: str = "Review the deployment",
    run_at: datetime | None = RUN_AT,
    cron: str | None = None,
) -> dict[str, Any]:
    return await store.create(
        user_id=user_id,
        workspace_id="personal",
        subagent_name=subagent_name,
        task=task,
        trigger_kind="cron" if cron else "once",
        run_at=run_at,
        cron=cron,
        timezone="UTC",
        manifest=_manifest(),
        manifest_hash="abc123",
    )


# --------------------------------------------------------------------------
# create + round-trip
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_persists_full_definition_and_frozen_manifest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        created = await _create_once(store)

        assert created["status"] == "scheduled"
        assert created["schedule_id"]
        assert created["trigger_kind"] == "once"
        assert created["run_at"] == RUN_AT.isoformat()
        assert created["cron"] is None
        assert created["manifest_hash"] == "abc123"

        loaded = await store.get("alice", created["schedule_id"])
        assert loaded is not None
        assert loaded["subagent_name"] == "researcher"
        assert loaded["task"] == "Review the deployment"
        assert loaded["workspace_id"] == "personal"
        assert loaded["manifest"] == _manifest()
        assert loaded["last_run_id"] is None
        assert loaded["last_error"] is None
        assert loaded["created_at"] == loaded["updated_at"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_recurring_schedule_round_trips_cron_and_timezone(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        created = await store.create(
            user_id="alice",
            workspace_id="personal",
            subagent_name="reporter",
            task="Daily digest",
            trigger_kind="cron",
            run_at=None,
            cron="0 8 * * *",
            timezone="Europe/Berlin",
            manifest=_manifest(),
            manifest_hash="def456",
        )

        loaded = await store.get("alice", created["schedule_id"])
        assert loaded is not None
        assert loaded["trigger_kind"] == "cron"
        assert loaded["cron"] == "0 8 * * *"
        assert loaded["timezone"] == "Europe/Berlin"
        assert loaded["run_at"] is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_schedule_ids_are_unique(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first = await _create_once(store)
        second = await _create_once(store)
        assert first["schedule_id"] != second["schedule_id"]
    finally:
        await store.close()


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rejects_trigger_kind_mismatched_with_its_fields(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="run_at"):
            await store.create(
                user_id="alice",
                workspace_id="personal",
                subagent_name="researcher",
                task="x",
                trigger_kind="once",
                run_at=None,
                cron="0 8 * * *",
                timezone="UTC",
                manifest=_manifest(),
                manifest_hash="h",
            )
        with pytest.raises(ValueError, match="cron"):
            await store.create(
                user_id="alice",
                workspace_id="personal",
                subagent_name="researcher",
                task="x",
                trigger_kind="cron",
                run_at=RUN_AT,
                cron=None,
                timezone="UTC",
                manifest=_manifest(),
                manifest_hash="h",
            )
        with pytest.raises(ValueError, match="trigger_kind"):
            await store.create(
                user_id="alice",
                workspace_id="personal",
                subagent_name="researcher",
                task="x",
                trigger_kind="whenever",
                run_at=RUN_AT,
                cron=None,
                timezone="UTC",
                manifest=_manifest(),
                manifest_hash="h",
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_rejects_blank_identity_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        for field in ("user_id", "workspace_id", "subagent_name", "task"):
            kwargs: dict[str, Any] = {
                "user_id": "alice",
                "workspace_id": "personal",
                "subagent_name": "researcher",
                "task": "Review",
                "trigger_kind": "once",
                "run_at": RUN_AT,
                "cron": None,
                "timezone": "UTC",
                "manifest": _manifest(),
                "manifest_hash": "h",
            }
            kwargs[field] = "  "
            with pytest.raises(ValueError, match=field):
                await store.create(**kwargs)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_rejected_create_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError):
            await store.create(
                user_id="alice",
                workspace_id="personal",
                subagent_name="researcher",
                task="x",
                trigger_kind="once",
                run_at=None,
                cron=None,
                timezone="UTC",
                manifest=_manifest(),
                manifest_hash="h",
            )
        assert await store.list_for_user("alice") == []
    finally:
        await store.close()


# --------------------------------------------------------------------------
# user isolation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_for_foreign_user_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        created = await _create_once(store, user_id="alice")
        assert await store.get("bob", created["schedule_id"]) is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_list_for_user_never_leaks_another_users_schedules(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        await _create_once(store, user_id="alice", subagent_name="researcher")
        await _create_once(store, user_id="alice", subagent_name="writer")
        await _create_once(store, user_id="bob", subagent_name="auditor")

        alice = await store.list_for_user("alice")
        bob = await store.list_for_user("bob")

        assert {row["subagent_name"] for row in alice} == {"researcher", "writer"}
        assert {row["subagent_name"] for row in bob} == {"auditor"}
        assert all(row["user_id"] == "alice" for row in alice)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_list_filters_by_status_and_paginates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first = await _create_once(store, subagent_name="a")
        await _create_once(store, subagent_name="b")
        third = await _create_once(store, subagent_name="c")
        await store.update_status("alice", third["schedule_id"], "cancelled")

        assert len(await store.list_for_user("alice")) == 3
        active = await store.list_for_user("alice", status="scheduled")
        assert {row["subagent_name"] for row in active} == {"a", "b"}
        # ORDER BY must be newest-first and is part of the contract.
        assert [r["subagent_name"] for r in await store.list_for_user("alice")] == ["c", "b", "a"]
        assert len(await store.list_for_user("alice", limit=1)) == 1
        page = await store.list_for_user("alice", limit=1, offset=1)
        assert len(page) == 1
        assert [r["subagent_name"] for r in page] == ["b"]
        assert first["schedule_id"] != page[0]["schedule_id"]
        with pytest.raises(ValueError, match="status"):
            await store.list_for_user("alice", status="nonsense")
    finally:
        await store.close()


# --------------------------------------------------------------------------
# status transitions + cancel
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_status_records_last_run_and_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        created = await _create_once(store)
        ok = await store.update_status(
            "alice", created["schedule_id"], "running", last_run_id="wq_123"
        )
        assert ok is True

        row = await store.get("alice", created["schedule_id"])
        assert row is not None
        assert row["status"] == "running"
        assert row["last_run_id"] == "wq_123"
        assert row["updated_at"] >= created["updated_at"]

        await store.update_status("alice", created["schedule_id"], "failed", last_error="boom")
        row = await store.get("alice", created["schedule_id"])
        assert row is not None
        assert row["status"] == "failed"
        assert row["last_error"] == "boom"
        # last_run_id is preserved unless explicitly replaced
        assert row["last_run_id"] == "wq_123"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_update_status_rejects_unknown_vocabulary(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        created = await _create_once(store)
        with pytest.raises(ValueError, match="status"):
            await store.update_status("alice", created["schedule_id"], "totally_fine")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_update_status_is_scoped_and_missing_is_false(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        created = await _create_once(store, user_id="alice")
        assert await store.update_status("bob", created["schedule_id"], "running") is False
        assert await store.update_status("alice", "nope", "running") is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancel_is_idempotent_and_user_scoped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        created = await _create_once(store, user_id="alice")

        assert await store.cancel("bob", created["schedule_id"]) is False
        assert await store.cancel("alice", created["schedule_id"]) is True
        # second cancel still reports success and does not move the status
        assert await store.cancel("alice", created["schedule_id"]) is True

        row = await store.get("alice", created["schedule_id"])
        assert row is not None
        assert row["status"] == "cancelled"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancel_does_not_overwrite_a_terminal_failure(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        created = await _create_once(store)
        await store.update_status("alice", created["schedule_id"], "failed", last_error="boom")

        await store.cancel("alice", created["schedule_id"])

        row = await store.get("alice", created["schedule_id"])
        assert row is not None
        assert row["status"] == "failed"
        assert row["last_error"] == "boom"
    finally:
        await store.close()


# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_for_restore_returns_only_restorable_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        once = await _create_once(store, subagent_name="once")
        cron = await store.create(
            user_id="alice",
            workspace_id="personal",
            subagent_name="cron",
            task="digest",
            trigger_kind="cron",
            run_at=None,
            cron="0 8 * * *",
            timezone="UTC",
            manifest=_manifest(),
            manifest_hash="h",
        )
        cancelled = await _create_once(store, subagent_name="gone")
        await store.cancel("alice", cancelled["schedule_id"])

        rows = await store.due_for_restore("alice")
        ids = {row["schedule_id"] for row in rows}

        assert once["schedule_id"] in ids
        assert cron["schedule_id"] in ids
        assert cancelled["schedule_id"] not in ids
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_due_for_restore_across_all_users_for_startup_reconciliation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        await _create_once(store, user_id="alice")
        await _create_once(store, user_id="bob")

        rows = await store.all_due_for_restore()
        assert {row["user_id"] for row in rows} == {"alice", "bob"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_rejects_naive_run_at(tmp_path: Path) -> None:
    """An unqualified trigger time is ambiguous across deployments."""
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            await store.create(
                user_id="alice",
                workspace_id="personal",
                subagent_name="researcher",
                task="x",
                trigger_kind="once",
                run_at=datetime(2026, 9, 26, 18, 0),  # noqa: DTZ001 - the point
                cron=None,
                timezone="UTC",
                manifest=_manifest(),
                manifest_hash="h",
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_due_for_restore_preserves_trigger_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        await _create_once(store, subagent_name="once")
        await store.create(
            user_id="alice",
            workspace_id="personal",
            subagent_name="cron",
            task="digest",
            trigger_kind="cron",
            run_at=None,
            cron="30 7 * * 1-5",
            timezone="America/New_York",
            manifest=_manifest(),
            manifest_hash="h",
        )

        rows = {row["subagent_name"]: row for row in await store.due_for_restore("alice")}

        assert rows["once"]["run_at"] == RUN_AT.isoformat()
        assert rows["once"]["cron"] is None
        assert rows["cron"]["cron"] == "30 7 * * 1-5"
        assert rows["cron"]["run_at"] is None
        assert rows["cron"]["timezone"] == "America/New_York"
    finally:
        await store.close()


# --------------------------------------------------------------------------
# resilience
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_database_recovers_to_an_empty_store(tmp_path: Path) -> None:
    store = SubagentScheduleStore(tmp_path / "nested" / "subagent_schedules.db")
    try:
        assert await store.list_for_user("alice") == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_reinitialises_when_the_file_is_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "subagent_schedules.db"
    path.write_bytes(b"this is definitely not a sqlite database")

    store = SubagentScheduleStore(path)
    try:
        assert await store.list_for_user("alice") == []
        created = await _create_once(store)
        assert await store.get("alice", created["schedule_id"]) is not None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_creates_do_not_collide(tmp_path: Path) -> None:

    store = _store(tmp_path)
    try:
        results = await asyncio.gather(
            *(_create_once(store, subagent_name=f"agent_{i}") for i in range(12))
        )
        ids = {row["schedule_id"] for row in results}
        assert len(ids) == 12
        assert len(await store.list_for_user("alice")) == 12
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_due_for_restore_requires_an_explicit_user() -> None:
    """The cross-user path must be named, not reachable by omitting an argument."""
    store = SubagentScheduleStore(":memory:")
    with pytest.raises(TypeError):
        store.due_for_restore()  # type: ignore[call-arg]
    assert store.all_due_for_restore.__doc__ is not None


def test_exported_vocabularies_are_consistent() -> None:
    assert TRIGGER_KINDS == frozenset({"once", "cron"})
    assert TERMINAL_STATUSES <= SCHEDULE_STATUSES
    assert "running" not in TERMINAL_STATUSES


# --------------------------------------------------------------------------
# transactional safety: a failed write must not leave a phantom schedule
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_create_leaves_no_phantom_schedule(tmp_path: Path) -> None:
    """A create() that raises before commit must not be flushed by a later write.

    Regression: the INSERT stayed in an open transaction on the shared
    connection, so the next writer's commit made a schedule durable that the
    caller never received an ID for — and `due_for_restore` would then fire it
    unattended.
    """
    store = _store(tmp_path)
    db = await store._get_db()
    real_commit = db.commit

    async def exploding_commit() -> None:
        raise RuntimeError("commit exploded")

    db.commit = exploding_commit  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="commit exploded"):
            await _create_once(store, subagent_name="PHANTOM")
    finally:
        db.commit = real_commit  # type: ignore[method-assign]

    assert db.in_transaction is False
    await _create_once(store, user_id="bob", subagent_name="real")
    await store.close()

    fresh = _store(tmp_path)
    try:
        assert await fresh.list_for_user("alice") == []
        assert [r["subagent_name"] for r in await fresh.all_due_for_restore()] == ["real"]
    finally:
        await fresh.close()


@pytest.mark.asyncio
async def test_failed_update_status_leaves_no_open_transaction(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = await _create_once(store)
    db = await store._get_db()
    real_commit = db.commit

    async def exploding_commit() -> None:
        raise RuntimeError("commit exploded")

    db.commit = exploding_commit  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="commit exploded"):
            await store.update_status("alice", created["schedule_id"], "running")
    finally:
        db.commit = real_commit  # type: ignore[method-assign]

    assert db.in_transaction is False
    row = await store.get("alice", created["schedule_id"])
    assert row is not None
    assert row["status"] == "scheduled"
    await store.close()


# --------------------------------------------------------------------------
# cancel() must not clobber an in-flight or completed run
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_does_not_overwrite_an_in_flight_run(tmp_path: Path) -> None:
    """A firing schedule keeps saying `running`; cancellation removes the trigger."""
    store = _store(tmp_path)
    try:
        created = await _create_once(store)
        await store.update_status(
            "alice", created["schedule_id"], "running", last_run_id="wq_live"
        )

        assert await store.cancel("alice", created["schedule_id"]) is True

        row = await store.get("alice", created["schedule_id"])
        assert row is not None
        assert row["status"] == "running"
        assert row["last_run_id"] == "wq_live"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancel_does_not_overwrite_a_completed_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        created = await _create_once(store)
        await store.update_status(
            "alice", created["schedule_id"], "completed", last_run_id="wq_done"
        )

        assert await store.cancel("alice", created["schedule_id"]) is True

        row = await store.get("alice", created["schedule_id"])
        assert row is not None
        assert row["status"] == "completed"
        assert row["last_run_id"] == "wq_done"
    finally:
        await store.close()


# --------------------------------------------------------------------------
# corrupt-database recovery must not leave replayable sidecars behind
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrupt_recovery_quarantines_the_file_and_drops_sidecars(
    tmp_path: Path,
) -> None:
    path = tmp_path / "subagent_schedules.db"
    store = _store(tmp_path)
    await store.close()

    path.write_bytes(b"not a database")
    # A non-empty -wal is a self-contained log that SQLite replays over a fresh
    # main file, which would resurrect the old rows *and* the old schema.
    (tmp_path / "subagent_schedules.db-wal").write_bytes(b"stale wal payload")
    (tmp_path / "subagent_schedules.db-shm").write_bytes(b"stale shm payload")

    recovered = SubagentScheduleStore(path)
    try:
        assert await recovered.list_for_user("alice") == []
    finally:
        await recovered.close()

    assert (tmp_path / "subagent_schedules.db.corrupt").exists()
    assert not (tmp_path / "subagent_schedules.db-wal").exists()
    assert not (tmp_path / "subagent_schedules.db-shm").exists()


@pytest.mark.asyncio
async def test_second_corruption_does_not_overwrite_the_first_quarantine(
    tmp_path: Path,
) -> None:
    path = tmp_path / "subagent_schedules.db"

    for attempt in range(2):
        path.write_bytes(f"garbage {attempt}".encode())
        (tmp_path / "subagent_schedules.db-wal").write_bytes(b"wal")
        # Force an actual connection so the recovery path really runs.
        recovered = SubagentScheduleStore(path)
        assert await recovered.all_due_for_restore() == []
        await recovered.close()

    quarantined = sorted(p.name for p in tmp_path.glob("subagent_schedules.db.corrupt*"))
    assert len(quarantined) == 2, quarantined


@pytest.mark.asyncio
async def test_close_is_serialized_against_an_in_flight_connect(tmp_path: Path) -> None:
    """close() must not return while a connect is still assigning self._db."""
    store = _store(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    real_open = store._open

    async def slow_open():
        started.set()
        await release.wait()
        return await real_open()

    store._open = slow_open  # type: ignore[method-assign]
    task = asyncio.create_task(store._get_db())
    await started.wait()

    closer = asyncio.create_task(store.close())
    release.set()
    await task
    await closer

    assert store._db is None


# --------------------------------------------------------------------------
# stricter durable-boundary validation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rejects_invalid_cron_expression(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="cron"):
            await store.create(
                user_id="alice",
                workspace_id="personal",
                subagent_name="researcher",
                task="x",
                trigger_kind="cron",
                run_at=None,
                cron="not a cron @@",
                timezone="UTC",
                manifest=_manifest(),
                manifest_hash="h",
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_rejects_unknown_timezone(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="timezone"):
            await store.create(
                user_id="alice",
                workspace_id="personal",
                subagent_name="researcher",
                task="x",
                trigger_kind="cron",
                run_at=None,
                cron="0 8 * * *",
                timezone="Nowhere/Bad",
                manifest=_manifest(),
                manifest_hash="h",
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_rejects_unparseable_run_at_string(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="run_at"):
            await store.create(
                user_id="alice",
                workspace_id="personal",
                subagent_name="researcher",
                task="x",
                trigger_kind="once",
                run_at="tomorrow at 8",
                cron=None,
                timezone="UTC",
                manifest=_manifest(),
                manifest_hash="h",
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_rejects_a_row_carrying_both_trigger_kinds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="both"):
            await store.create(
                user_id="alice",
                workspace_id="personal",
                subagent_name="researcher",
                task="x",
                trigger_kind="once",
                run_at=RUN_AT,
                cron="0 8 * * *",
                timezone="UTC",
                manifest=_manifest(),
                manifest_hash="h",
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_rejects_a_non_date_run_at(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError):
            await store.create(
                user_id="alice",
                workspace_id="personal",
                subagent_name="researcher",
                task="x",
                trigger_kind="once",
                run_at=12345,  # type: ignore[arg-type]
                cron=None,
                timezone="UTC",
                manifest=_manifest(),
                manifest_hash="h",
            )
    finally:
        await store.close()


# --------------------------------------------------------------------------
# manifest integrity + error clearing
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrupt_manifest_is_reported_not_silently_emptied(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = await _create_once(store)
    await store.close()

    async with aiosqlite.connect(tmp_path / "subagent_schedules.db") as db:
        await db.execute(
            "UPDATE subagent_schedules SET manifest_json = ? WHERE id = ?",
            ("{not json", created["schedule_id"]),
        )
        await db.commit()

    reopened = _store(tmp_path)
    try:
        row = await reopened.get("alice", created["schedule_id"])
        assert row is not None
        # A record whose manifest provably does not match its hash must be
        # visibly invalid, never served as an empty-but-trusted manifest.
        assert row["manifest_valid"] is False
        assert row["manifest"] is None
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_update_status_can_clear_a_stale_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        created = await _create_once(store)
        await store.update_status("alice", created["schedule_id"], "failed", last_error="boom")
        await store.update_status("alice", created["schedule_id"], "running", clear_error=True)

        row = await store.get("alice", created["schedule_id"])
        assert row is not None
        assert row["status"] == "running"
        assert row["last_error"] is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_a_concurrent_writer_cannot_flush_a_failed_write(tmp_path: Path) -> None:
    """Two writers on the shared connection must not cross-contaminate commits.

    sqlite3's implicit transaction is per *connection*: without write
    serialization a second writer's commit() flushes the first writer's pending
    DML, and the first writer's rollback() is then a no-op.
    """
    store = _store(tmp_path)
    try:
        results = await asyncio.gather(
            _create_once(store, user_id="alice", subagent_name="A"),
            _create_once(store, user_id="bob", subagent_name="B"),
            _create_once(store, user_id="carol", subagent_name="C"),
            return_exceptions=True,
        )
        assert not [r for r in results if isinstance(r, BaseException)], results
        assert len(await store.list_for_user("alice")) == 1
        assert len(await store.list_for_user("bob")) == 1
        assert len(await store.list_for_user("carol")) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_a_terminal_row_cannot_be_silently_rearmed(tmp_path: Path) -> None:
    """Re-arming a cancelled row would put it back in front of startup restore."""
    store = _store(tmp_path)
    try:
        created = await _create_once(store)
        await store.cancel("alice", created["schedule_id"])

        assert await store.update_status("alice", created["schedule_id"], "scheduled") is False

        row = await store.get("alice", created["schedule_id"])
        assert row is not None
        assert row["status"] == "cancelled"
        assert await store.due_for_restore("alice") == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_migration_can_opt_into_reopening(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        created = await _create_once(store)
        await store.update_status("alice", created["schedule_id"], "needs_review")

        # needs_review is not a stop decision, so the normal path re-arms it.
        assert (
            await store.update_status("alice", created["schedule_id"], "scheduled") is True
        )
        row = await store.get("alice", created["schedule_id"])
        assert row is not None
        assert row["status"] == "scheduled"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_a_recurring_schedule_reruns_after_an_outcome(tmp_path: Path) -> None:
    """`failed`/`completed` are outcomes, not stop decisions — cron must re-fire."""
    store = _store(tmp_path)
    try:
        created = await _create_once(store, run_at=None, cron="0 8 * * *")
        for outcome in ("failed", "completed", "running"):
            assert await store.update_status("alice", created["schedule_id"], outcome)
            assert (
                await store.update_status("alice", created["schedule_id"], "running")
            )
        row = await store.get("alice", created["schedule_id"])
        assert row is not None
        assert row["status"] == "running"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_a_cancelled_row_needs_the_explicit_reopen_opt_in(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        created = await _create_once(store)
        await store.cancel("alice", created["schedule_id"])

        assert await store.update_status("alice", created["schedule_id"], "scheduled") is False
        assert (
            await store.update_status(
                "alice",
                created["schedule_id"],
                "scheduled",
                allow_terminal_reopen=True,
            )
            is True
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_works_as_an_async_context_manager(tmp_path: Path) -> None:
    async with _store(tmp_path) as store:
        created = await _create_once(store)
        assert created["schedule_id"]
    assert store._db is None



@pytest.mark.asyncio
async def test_manifest_is_stored_as_json_text(tmp_path: Path) -> None:
    """The frozen manifest must round-trip as structured JSON, not a string."""
    store = _store(tmp_path)
    created = await _create_once(store)
    await store.close()

    async with aiosqlite.connect(tmp_path / "subagent_schedules.db") as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT manifest_json FROM subagent_schedules WHERE id = ?",
            (created["schedule_id"],),
        )
        row = await cursor.fetchone()

    assert row is not None
    assert isinstance(json.loads(row["manifest_json"]), dict)
