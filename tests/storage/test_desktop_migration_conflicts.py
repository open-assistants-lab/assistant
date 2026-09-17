"""Regression coverage for ambiguous legacy desktop promotion targets."""

import pytest

from src.storage.desktop_migration import run_desktop_migration


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
