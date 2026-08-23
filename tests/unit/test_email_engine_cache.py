"""Engine-cache tests for email_db (audit P1).

`get_engine` previously built a fresh SQLAlchemy engine + ran the full DDL
on EVERY invocation and never disposed it — pooled SQLite connections piled
up until GC. It must now cache one engine per user and run init_db exactly
once per user."""

from __future__ import annotations

from typing import Any

import pytest

from src.sdk.tools_core import email_db


@pytest.fixture()
def isolated_engines(monkeypatch: pytest.MonkeyPatch, tmp_path: Any):
    """Fresh cache + tmp-backed db paths so tests never touch user data."""
    monkeypatch.setattr(email_db, "_engines", {})
    counters = {"init_db": 0}
    real_init_db = email_db.init_db

    def counting_init_db(engine: Any) -> None:
        counters["init_db"] += 1
        real_init_db(engine)

    monkeypatch.setattr(email_db, "init_db", counting_init_db)

    def fake_get_db_path(user_id: str) -> str:
        return str(tmp_path / f"{user_id or 'default_user'}.db")

    monkeypatch.setattr(email_db, "get_db_path", fake_get_db_path)
    return counters


def test_get_engine_returns_same_engine_per_user(isolated_engines):
    first = email_db.get_engine("cache_user")
    second = email_db.get_engine("cache_user")
    assert first is second


def test_get_engine_distinct_users_get_distinct_engines(isolated_engines):
    a = email_db.get_engine("cache_user_a")
    b = email_db.get_engine("cache_user_b")
    assert a is not b


def test_init_db_runs_once_per_user(isolated_engines):
    email_db.get_engine("counter_user")
    email_db.get_engine("counter_user")
    email_db.get_engine("counter_user")
    assert isolated_engines["init_db"] == 1
