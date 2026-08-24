"""Scheduler DB decay cutoff + locked lazy init (audit B10).

`_apply_decay` previously used `updated_at < now()` (tautologically true)
and never bumped `updated_at`, so every cycle shaved 0.01 off EVERY fact.
Both DB classes also initialised their aiosqlite connection without a lock,
so concurrent first callers could leak connections.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from src.sdk.tools_core.agent_scheduler_db import (
    SchedulerMemoryDB,
    SchedulerNotificationDB,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _fake_get_paths(user_id: str, **_: Any):
    from src.storage.paths import DataPaths

    return DataPaths(
        ea_root=str(_TMP_ROOT),
        data_path=str(Path(_TMP_ROOT) / "data"),
        user_id=user_id,
    )


_TMP_ROOT: str = ""


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    global _TMP_ROOT
    _TMP_ROOT = str(tmp_path)

    def _get_paths(user_id: str, **kwargs: Any):  # noqa: ANN001, ANN003
        from src.storage.paths import DataPaths

        return DataPaths(
            ea_root=str(tmp_path),
            data_path=str(tmp_path / "data"),
            user_id=user_id,
        )

    monkeypatch.setattr(
        "src.sdk.tools_core.agent_scheduler_db.get_paths", _get_paths
    )
    return tmp_path


@pytest.mark.asyncio
class TestDecayCutoff:
    async def test_decay_only_touches_facts_older_than_one_day(self, isolated):
        db = SchedulerMemoryDB("u1")
        now = datetime.now(UTC)
        await db.upsert_fact("fresh", "v", confidence=0.9, updated_at=_iso(now))
        await db.upsert_fact(
            "stale", "v", confidence=0.9, updated_at=_iso(now - timedelta(days=2))
        )

        await db._apply_decay()

        fresh = await db.get_fact("fresh")
        stale = await db.get_fact("stale")
        assert fresh is not None and stale is not None
        assert fresh["confidence"] == pytest.approx(0.9)
        assert stale["confidence"] == pytest.approx(0.89)

    async def test_decay_bumps_updated_at_so_stale_facts_do_not_redecay(self, isolated):
        """With the fix, updated_at is bumped to now — an immediate second
        decay cycle must NOT shave again (the old bug re-decayed every cycle)."""
        db = SchedulerMemoryDB("u1")
        old = datetime.now(UTC) - timedelta(days=2)
        await db.upsert_fact("k", "v", confidence=0.9, updated_at=_iso(old))

        await db._apply_decay()
        once = (await db.get_fact("k"))["confidence"]

        await db._apply_decay()
        twice = (await db.get_fact("k"))["confidence"]

        assert once == pytest.approx(0.89)
        assert twice == pytest.approx(0.89)

    async def test_upsert_fact_updates_existing_row(self, isolated):
        db = SchedulerMemoryDB("u1")
        await db.upsert_fact("k", "v1", confidence=0.5)
        await db.upsert_fact("k", "v2", confidence=0.8)

        fact = await db.get_fact("k")
        assert fact is not None
        assert fact["value"] == "v2"
        assert fact["confidence"] == pytest.approx(0.8)

    async def test_get_fact_missing_returns_none(self, isolated):
        db = SchedulerMemoryDB("u1")
        assert await db.get_fact("nope") is None


@pytest.mark.asyncio
class TestLockedLazyInit:
    async def test_memory_concurrent_get_db_single_connection(self, isolated):
        db = SchedulerMemoryDB("u1")
        conns = await asyncio.gather(*(db._get_db() for _ in range(20)))
        assert len({id(c) for c in conns}) == 1
        await db.close()

    async def test_notification_concurrent_get_db_single_connection(self, isolated):
        db = SchedulerNotificationDB("u1")
        conns = await asyncio.gather(*(db._get_db() for _ in range(20)))
        assert len({id(c) for c in conns}) == 1
        await db.close()
