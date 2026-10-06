"""Surviving misc correctness checks: message workspace reporting, SQL dates, spill TTL.

Removed-subject todo UUID and contacts uniqueness/migration tests are retired;
these are properties of the deleted stores, not generic governance assertions.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from src.sdk.tools_core import apps as apps_mod
from src.sdk.tools_core import message as message_mod
from src.sdk.tools_core import shell as shell_mod


class _FakeStore:
    def search_hybrid(self, q: str, limit: int = 10):
        return []


def _fn(obj):
    return obj.function if hasattr(obj, "function") else obj


def test_message_count_reports_only_queried_workspace(monkeypatch):
    monkeypatch.setattr(message_mod, "get_message_store", lambda u, w: _FakeStore())
    monkeypatch.setattr(
        message_mod, "_list_workspace_ids", lambda u: ["personal", "acme-corp", "side-hustle"]
    )
    monkeypatch.setattr(message_mod, "expand_queries", lambda q, llm_provider=None: [q])
    monkeypatch.setattr(message_mod, "_try_create_llm_provider", lambda: None)
    out = _fn(message_mod.message_count)("kits", user_id="u1", workspace_id="personal")
    assert "Searched 1 workspace" in out
    assert "acme-corp" not in out
    assert "side-hustle" not in out


def test_convert_date_leaves_singlequoted_literals_untouched():
    q = "SELECT * WHERE note LIKE '%today%' AND created > today"
    out = apps_mod._convert_date_in_query(q)
    assert "'%today%'" in out, "string literal must not be rewritten"
    assert out != q, "bare date word outside the literal must be rewritten"


def test_convert_date_last_month_january_resolves_previous_december(monkeypatch):
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 15)
    monkeypatch.setattr(apps_mod, "datetime", _FrozenDatetime)
    out = apps_mod._convert_date_in_query("created > last month")
    expected = str(int(datetime(2025, 12, 1).timestamp() * 1000))
    assert expected in out


def test_convert_date_escaped_quote_hardening_pin():
    q = "SELECT * WHERE msg LIKE '%'' today%' AND d > today AND m = 'last month'"
    out = apps_mod._convert_date_in_query(q)
    assert "'%'' today%'" in out
    assert "'last month'" in out
    today = str(int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000))
    assert out.count(today) == 1


def test_convert_date_handles_escaped_quotes_in_literals():
    q = "SELECT * WHERE msg LIKE '%'' today%' AND d > today"
    out = apps_mod._convert_date_in_query(q)
    today_epoch = str(
        int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    )
    assert out.count(today_epoch) == 1
    assert "'%'' today%'" in out


def test_sweep_old_spill_files_deletes_only_stale(tmp_path: Path):
    out_dir = tmp_path / ".shell_output"
    out_dir.mkdir()
    old = out_dir / "output-20200101-000000-000000.txt"
    recent = out_dir / "output-20990101-000000-000000.txt"
    old.write_text("old")
    recent.write_text("recent")
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    removed = shell_mod._sweep_old_spill_files(out_dir, max_age_days=7)
    assert removed == 1
    assert not old.exists()
    assert recent.exists()


def test_sweep_missing_directory_is_noop(tmp_path: Path):
    assert shell_mod._sweep_old_spill_files(tmp_path / "nope") == 0
