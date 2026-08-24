"""tools_core misc correctness sweep (audit B17 remainder).

Covers: honest message_count workspace reporting, full-UUID todo ids,
apps date-word rewriting outside string literals + January "last month",
contacts email UNIQUE index with dedupe migration + upsert + last_name
clearing, and shell spill-file TTL sweep."""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError as SAIntegrityError

from src.sdk.tools_core import apps as apps_mod
from src.sdk.tools_core import contacts_storage as cs
from src.sdk.tools_core import message as message_mod
from src.sdk.tools_core import shell as shell_mod
from src.sdk.tools_core import todos_storage as todos_mod


# ---------------------------------------------------------------- 1. message_count


class _FakeStore:
    def search_hybrid(self, q: str, limit: int = 10):
        return []


def _fn(obj):
    """Unwrap @tool ToolDefinition wrappers to the raw callable."""
    return obj.function if hasattr(obj, "function") else obj


def test_message_count_reports_only_queried_workspace(monkeypatch):
    """Output must not claim workspaces that were never queried."""
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


# ---------------------------------------------------------------- 2. todo ids


def test_add_todo_uses_full_uuid(tmp_path, monkeypatch):
    monkeypatch.setattr(todos_mod, "get_db_path", lambda u: str(tmp_path / f"{u}.db"))
    todo = _fn(todos_mod.add_todo)("u1", "write tests")
    assert len(todo["id"]) == 36
    assert todo["id"].count("-") == 4


# ---------------------------------------------------------------- 3. apps date rewrite


def test_convert_date_leaves_singlequoted_literals_untouched():
    q = "SELECT * WHERE note LIKE '%today%' AND created > today"
    out = apps_mod._convert_date_in_query(q)
    assert "'%today%'" in out, "string literal must not be rewritten"
    assert out != q, "bare date word outside the literal must be rewritten"


def test_convert_date_last_month_january_resolves_previous_december(monkeypatch):
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return datetime(2026, 1, 15)

    monkeypatch.setattr(apps_mod, "datetime", _FrozenDatetime)
    out = apps_mod._convert_date_in_query("created > last month")
    expected = str(int(datetime(2025, 12, 1).timestamp() * 1000))
    assert expected in out


# ---------------------------------------------------------------- 4. contacts


@pytest.fixture()
def contacts_iso(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(cs, "_engines", {})

    def fake_get_db_path(user_id: str) -> str:
        return str(tmp_path / f"{user_id or 'default_user'}-contacts.db")

    monkeypatch.setattr(cs, "get_db_path", fake_get_db_path)
    return tmp_path


def test_contacts_email_unique_index_enforced(contacts_iso):
    cs.add_contact("cu1", "dup@x.com", name="First Last")
    engine = cs.get_engine("cu1")
    with engine.connect() as conn:
        from sqlalchemy import text

        with pytest.raises(SAIntegrityError):
            conn.execute(
                text(
                    "INSERT INTO contacts (id, email, name, source, created_at) "
                    "VALUES ('other', 'dup@x.com', 'Other', 'manual', 0)"
                )
            )


def test_save_contacts_upserts_on_duplicate_email(contacts_iso):
    n1 = cs.save_contacts("cu2", "acct", [{"email": "a@x.com", "name": ""}])
    n2 = cs.save_contacts("cu2", "acct", [{"email": "a@x.com", "name": "Real Name"}])
    assert n1 == 1 and n2 == 0  # no duplicate row created
    contact = cs.get_contact("cu2", email="a@x.com")
    assert contact is not None
    rows = cs.get_engine("cu2").connect().exec_driver_sql(
        "SELECT COUNT(*) FROM contacts WHERE email='a@x.com'"
    ).scalar()
    assert rows == 1
    # name backfill preserved from the upsert path
    assert contact["name"] in ("Real Name", None, "")


def test_update_contact_single_word_name_clears_last_name(contacts_iso):
    added = cs.add_contact("cu3", "john@x.com", name="John Smith")
    assert added.get("success", True)
    result = cs.update_contact("cu3", email="john@x.com", name="Madonna")
    assert result.get("success", True)
    contact = cs.get_contact("cu3", email="john@x.com")
    assert contact is not None
    assert contact["last_name"] in (None, "")


def test_init_db_migrates_legacy_db_dedupe_and_unique_index(tmp_path):
    legacy = tmp_path / "legacy-contacts.db"
    conn = sqlite3.connect(str(legacy))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS contacts ("
        " id TEXT PRIMARY KEY, email TEXT, name TEXT, first_name TEXT,"
        " last_name TEXT, phone TEXT, company TEXT, source TEXT,"
        " email_account TEXT, created_at INTEGER, updated_at INTEGER)"
    )
    now = int(datetime.now(UTC).timestamp())
    conn.execute(
        "INSERT INTO contacts VALUES ('keep','d@x.com','D','D',NULL,NULL,NULL,'email','a',?,NULL)",
        (now,),
    )
    conn.execute(
        "INSERT INTO contacts VALUES ('dupe','d@x.com','Dup','Dup',NULL,NULL,NULL,'email','a',?,NULL)",
        (now + 1,),
    )
    conn.commit()
    conn.close()

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{legacy}")
    cs._init_db(engine)

    count = engine.connect().exec_driver_sql(
        "SELECT COUNT(*) FROM contacts WHERE email='d@x.com'"
    ).scalar()
    assert count == 1
    # unique index now enforced
    with engine.connect() as conn:
        with pytest.raises(SAIntegrityError):
            conn.exec_driver_sql(
                "INSERT INTO contacts (id,email,name,source,created_at) "
                "VALUES ('x2','d@x.com','X','email',0)"
            )


def test_save_contacts_cross_source_no_dangling_side_insert(contacts_iso):
    """Manual contact + later email-sync of the same address must not create
    a dangling contact_emails row nor count a phantom new contact (audit B17
    fix round: unique index spans ALL sources, the old pre-check did not)."""
    cs.add_contact("cx1", "shared@x.com", name="Manual Person")
    n = cs.save_contacts("cx1", "acct", [{"email": "shared@x.com", "name": "Sync Name"}])
    assert n == 0
    engine = cs.get_engine("cx1")
    rows = engine.connect().exec_driver_sql(
        "SELECT COUNT(*) FROM contacts WHERE email='shared@x.com'"
    ).scalar()
    assert rows == 1
    orphans = engine.connect().exec_driver_sql(
        "SELECT COUNT(*) FROM contact_emails ce "
        "LEFT JOIN contacts c ON c.id = ce.contact_id WHERE c.id IS NULL"
    ).scalar()
    assert orphans == 0


def test_convert_date_escaped_quote_hardening_pin():
    """Hardening pin (fix round 1 / F2): escaped quotes ('') inside literals
    survive date rewriting. On WELL-FORMED SQL the old parity split behaved
    identically (provably — each literal contributes an even number of quote
    chars); this pins the sequential-walker replacement against regressions.
    Known limitation (both approaches): a lone apostrophe inside a literal
    makes the remainder conservative (unrewritten)."""
    q = "SELECT * WHERE msg LIKE '%'' today%' AND d > today AND m = 'last month'"
    out = apps_mod._convert_date_in_query(q)
    assert "'%'' today%'" in out
    assert "'last month'" in out
    assert out.count(str(int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000))) == 1


def test_convert_date_handles_escaped_quotes_in_literals():
    """SQL doubled quotes ('') must not flip the in-literal parity."""
    q = "SELECT * WHERE msg LIKE '%'' today%' AND d > today"
    out = apps_mod._convert_date_in_query(q)
    today_epoch = str(
        int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    )
    assert out.count(today_epoch) == 1
    assert "'%'' today%'" in out


# ---------------------------------------------------------------- 5. shell spill TTL


def test_sweep_old_spill_files_deletes_only_stale(tmp_path: Path):
    out_dir = tmp_path / ".shell_output"
    out_dir.mkdir()
    old = out_dir / "output-20200101-000000-000000.txt"
    recent = out_dir / "output-20990101-000000-000000.txt"
    old.write_text("old")
    recent.write_text("recent")
    ten_days_ago = time.time() - 10 * 86400
    os.utime(old, (ten_days_ago, ten_days_ago))

    removed = shell_mod._sweep_old_spill_files(out_dir, max_age_days=7)

    assert removed == 1
    assert not old.exists()
    assert recent.exists()


def test_sweep_missing_directory_is_noop(tmp_path: Path):
    assert shell_mod._sweep_old_spill_files(tmp_path / "nope") == 0
