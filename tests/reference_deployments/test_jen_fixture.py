"""Catch stale/ambiguous writes and accidental reseeding of mutable state."""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from examples.jen_reference.fixture import initialise_state, read_store, set_pause

PACKAGE = Path(__file__).resolve().parents[2] / "examples/jen_reference"


@pytest.fixture
def db(tmp_path):
    db = tmp_path / "with spaces/state.sqlite3"
    initialise_state(db, PACKAGE / "domain.json")
    return db


def test_read_change_noop_and_reinitialise(db):
    assert read_store(db, "fixture-alpha")["revision"] == 1
    assert set_pause(db, "fixture-alpha", True, 1) == {
        "ok": True, "outcome": "changed", "store_id": "fixture-alpha", "paused": True, "revision": 2}
    assert set_pause(db, "fixture-alpha", True, 2)["outcome"] == "noop"
    initialise_state(db, PACKAGE / "domain.json")
    assert read_store(db, "fixture-alpha")["revision"] == 2
    assert read_store(db, "fixture-beta")["paused"] is False


@pytest.mark.parametrize("store_id", ["alpha", "unknown", "fixture", "../other", ""])
def test_unknown_or_ambiguous_target_never_mutates(db, store_id):
    with pytest.raises(ValueError, match="unknown_target"):
        set_pause(db, store_id, True, 1)
    assert read_store(db, "fixture-alpha")["paused"] is False


def test_stale_revision_and_invalid_bool_never_mutate(db):
    with pytest.raises(ValueError, match="stale_revision"):
        set_pause(db, "fixture-alpha", True, 999)
    with pytest.raises(ValueError, match="invalid_pause"):
        set_pause(db, "fixture-alpha", "false", 1)
    assert read_store(db, "fixture-alpha")["revision"] == 1


def test_only_one_same_revision_writer_succeeds(db):
    def write(_):
        try:
            return set_pause(db, "fixture-alpha", True, 1)["outcome"]
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == ["changed", "stale_revision"]
    assert read_store(db, "fixture-alpha")["revision"] == 2


def test_cli_spaced_paths_and_redacted_error(db):
    args = [sys.executable, str(PACKAGE / "fixture.py"), "--state", str(db), "read", "--store-id", "fixture-alpha"]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["paused"] is False
    args[-1] = "secret-like-target"
    failed = subprocess.run(args, capture_output=True, text=True)
    assert failed.returncode == 1
    assert json.loads(failed.stdout) == {"ok": False, "error": "unknown_target"}
    assert str(db) not in failed.stdout + failed.stderr
