"""Tests for companion V1: CompanionDB, CompanionScheduler, HTTP endpoints."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

os.environ.setdefault("AGENT_MODEL", "ollama:minimax-m2.5")


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mock_paths(tmp_dir):
    from src.storage.paths import DataPaths

    mock_dp = DataPaths(data_path=tmp_dir, user_id="test_user", ea_root=tmp_dir)
    with patch("src.sdk.tools_core.companion_db.get_paths", return_value=mock_dp):
        yield mock_dp


class TestCompanionNotificationDB:
    async def test_insert_and_list(self, mock_paths):
        from src.sdk.tools_core.companion_db import CompanionNotificationDB

        db = CompanionNotificationDB("test_user")
        try:
            nid = await db.insert("Good morning!", "checkin")
            assert nid

            notifs = await db.list(limit=50)
            assert len(notifs) == 1
            assert notifs[0]["message"] == "Good morning!"
            assert notifs[0]["category"] == "checkin"
            assert notifs[0]["dismissed"] == 0
        finally:
            await db.close()

    async def test_insert_with_category(self, mock_paths):
        from src.sdk.tools_core.companion_db import CompanionNotificationDB

        db = CompanionNotificationDB("test_user")
        try:
            await db.insert("3 overdue tasks", "deadline")
            notifs = await db.list(limit=50)
            assert notifs[0]["category"] == "deadline"
        finally:
            await db.close()

    async def test_dismiss(self, mock_paths):
        from src.sdk.tools_core.companion_db import CompanionNotificationDB

        db = CompanionNotificationDB("test_user")
        try:
            nid = await db.insert("Test notification", "general")
            ok = await db.dismiss(nid)
            assert ok

            notifs = await db.list(limit=50)
            assert len(notifs) == 0

            notifs_all = await db.list(limit=50, include_dismissed=True)
            assert len(notifs_all) == 1
            assert notifs_all[0]["dismissed"] == 1
        finally:
            await db.close()

    async def test_dismiss_nonexistent(self, mock_paths):
        from src.sdk.tools_core.companion_db import CompanionNotificationDB

        db = CompanionNotificationDB("test_user")
        try:
            ok = await db.dismiss("nonexistent-id")
            assert not ok
        finally:
            await db.close()

    async def test_recent_messages(self, mock_paths):
        from src.sdk.tools_core.companion_db import CompanionNotificationDB

        db = CompanionNotificationDB("test_user")
        try:
            await db.insert("First", "checkin")
            await db.insert("Second", "general")
            await db.insert("Third", "deadline")
            recent = await db.recent_messages(2)
            assert "Third" in recent
            assert "Second" in recent
            assert "First" not in recent
        finally:
            await db.close()

    async def test_last_check_time(self, mock_paths):
        from src.sdk.tools_core.companion_db import CompanionNotificationDB

        db = CompanionNotificationDB("test_user")
        try:
            assert (await db.last_check_time()) == "never"
            await db.insert("Test", "checkin")
            last = await db.last_check_time()
            assert last != "never"
        finally:
            await db.close()

    async def test_dismissal_streak(self, mock_paths):
        from src.sdk.tools_core.companion_db import CompanionNotificationDB

        db = CompanionNotificationDB("test_user")
        try:
            assert (await db.dismissal_streak()) == 0

            nid2 = await db.insert("Second", "general")
            nid3 = await db.insert("Third", "general")
            nid4 = await db.insert("Fourth", "general")

            await db.dismiss(nid4)
            await db.dismiss(nid3)
            await db.dismiss(nid2)

            assert (await db.dismissal_streak()) == 3
        finally:
            await db.close()


class TestCompanionMemoryDB:
    async def test_list_empty(self, mock_paths):
        from src.sdk.tools_core.companion_db import CompanionMemoryDB

        db = CompanionMemoryDB("test_user")
        try:
            facts = await db.get_all()
            assert facts == {}

            facts_list = await db.list_all()
            assert facts_list == []
        finally:
            await db.close()

    async def test_delete(self, mock_paths):
        from src.sdk.tools_core.companion_db import CompanionMemoryDB

        db = CompanionMemoryDB("test_user")
        try:
            ok = await db.delete(99999)
            assert not ok
        finally:
            await db.close()


class TestCompanionScheduler:
    async def test_categorize_general(self):
        from src.sdk.companion_scheduler import CompanionScheduler

        cat, ws = CompanionScheduler._categorize("Your Pipeline is looking green today.")
        assert cat == "general"
        assert ws is None

    async def test_categorize_checkin(self):
        from src.sdk.companion_scheduler import CompanionScheduler

        cat, ws = CompanionScheduler._categorize("Good morning! How are you today?")
        assert cat == "checkin"
        assert ws is None

    async def test_categorize_urgent(self):
        from src.sdk.companion_scheduler import CompanionScheduler

        cat, ws = CompanionScheduler._categorize("URGENT: Server down in production!")
        assert cat == "urgent"
        assert ws is None

    async def test_categorize_deadline(self):
        from src.sdk.companion_scheduler import CompanionScheduler

        cat, ws = CompanionScheduler._categorize("You have 3 overdue tasks due today.")
        assert cat == "deadline"
        assert ws is None

    async def test_categorize_email(self):
        from src.sdk.companion_scheduler import CompanionScheduler

        cat, ws = CompanionScheduler._categorize("New email from Sarah about the budget.")
        assert cat == "email"
        assert ws is None

    async def test_extract_response(self):
        from src.sdk.companion_scheduler import CompanionScheduler
        from src.sdk.messages import Message

        result = [Message.user("ctx"), Message.assistant("Hello there!")]
        text = CompanionScheduler._extract_response(result)
        assert "Hello" in text

    async def test_extract_response_skip(self):
        from src.sdk.companion_scheduler import CompanionScheduler
        from src.sdk.messages import Message

        result = [Message.user("ctx"), Message.assistant("[SKIP]")]
        text = CompanionScheduler._extract_response(result)
        assert text == "[SKIP]"

    async def test_extract_response_empty(self):
        from src.sdk.companion_scheduler import CompanionScheduler
        from src.sdk.messages import Message

        result = [Message.assistant("")]
        text = CompanionScheduler._extract_response(result)
        assert text == ""

    async def test_next_interval_default(self, tmp_dir):
        from src.storage.paths import DataPaths

        mock_dp = DataPaths(data_path=tmp_dir, user_id="test_user", ea_root=tmp_dir)
        with patch("src.sdk.tools_core.companion_db.get_paths", return_value=mock_dp):
            from src.sdk.companion_scheduler import CompanionScheduler

            scheduler = CompanionScheduler("test_user")
            interval = await scheduler._next_interval()
            assert interval == 15

    async def test_time_of_day(self):
        from src.sdk.companion_scheduler import _time_of_day

        assert _time_of_day(8) == "morning"
        assert _time_of_day(14) == "afternoon"
        assert _time_of_day(20) == "evening"

    async def test_relative_time(self):
        from src.sdk.companion_scheduler import _relative_time

        now = datetime.now(UTC).replace(tzinfo=None)
        assert _relative_time(now) in ("just now",) or "ago" in _relative_time(now)
        five_min = now - timedelta(minutes=5)
        assert "5m ago" in _relative_time(five_min)
        two_hours = now - timedelta(hours=2)
        assert "2h ago" in _relative_time(two_hours)


class TestDataPaths:
    def test_companion_paths(self, tmp_dir):
        from src.storage.paths import DataPaths

        dp = DataPaths(data_path=tmp_dir, user_id="test_user")
        assert dp.companion_dir().exists()
        assert dp.companion_notifications_db().parent.exists()
        assert dp.companion_memory_db().parent.exists()
