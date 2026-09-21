"""Regression coverage for ambiguous legacy desktop promotion targets."""

import json

import pytest

from src.storage.desktop_migration import RECOVERY_FILE, run_desktop_migration


def test_duplicate_legacy_message_targets_stop_before_moving(tmp_path):
    root = tmp_path / "Assistant"
    legacy = root / "Users" / "native_sdk_chat"
    for name in ("Conversation", "Messages"):
        source = legacy / name
        source.mkdir(parents=True)
        (source / "history.db").write_bytes(name.encode())

    with pytest.raises(SystemExit):
        run_desktop_migration(root)

    for name in ("Conversation", "Messages"):
        assert (legacy / name / "history.db").read_bytes() == name.encode()
    assert not (root / "Messages").exists()


def test_unexpected_users_sibling_stops_before_moving(tmp_path):
    """A non-`native_sdk_chat` sibling under `Users/` must trigger recovery.

    The legacy promotion moves the children of `Users/native_sdk_chat` to the
    data root; an unexpected sibling means the `Users/` tree is not the shape
    the migration expects, so it must stop before touching anything.
    """
    root = tmp_path / "Assistant"
    legacy = root / "Users" / "native_sdk_chat"
    (legacy / "Logs").mkdir(parents=True)
    (legacy / "Logs" / "app.log").write_text("legacy")
    sibling = root / "Users" / "other_user"
    sibling.mkdir(parents=True)
    (sibling / "keep.txt").write_text("untouched")

    with pytest.raises(SystemExit):
        run_desktop_migration(root)

    recovery = json.loads((root / ".system" / RECOVERY_FILE).read_text())
    assert recovery["requires_recovery"] is True
    assert "users/other_user" in recovery["sources"]

    # nothing moved, nothing deleted
    assert not (root / "Logs").exists()
    assert legacy.exists()
    assert (legacy / "Logs" / "app.log").read_text() == "legacy"
    assert (sibling / "keep.txt").read_text() == "untouched"
