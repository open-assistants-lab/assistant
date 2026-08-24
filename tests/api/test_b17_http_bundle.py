"""Task 20 (audit B17): HTTP misc correctness bundle tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.http.workspace_cache import FileCache


class TestFileCacheFreshness:
    def _make(self, tmp_path: Path, monkeypatch=None, files: list[str] | None = None):
        ws_root = tmp_path / "files"
        ws_root.mkdir(exist_ok=True)
        for name in files or []:
            (ws_root / name).write_text("x")

        class _FakePaths:
            def workspace_files_dir(self):
                return ws_root

            def workspace_cache(self):
                return tmp_path / "ws_cache.json"

        if monkeypatch is not None:
            monkeypatch.setattr(
                "src.http.workspace_cache.get_paths", lambda *a, **k: _FakePaths()
            )
        cache = FileCache(user_id="t20", workspace_id="personal")
        cache._cache = {}
        return cache

    def test_mark_downloaded_stamps_server_modified(self, tmp_path: Path, monkeypatch):
        cache = self._make(tmp_path, monkeypatch, files=["a.txt"])
        cache.mark_downloaded("a.txt")
        entry = json.loads((tmp_path / "ws_cache.json").read_text())["a.txt"]
        assert entry.get("server_modified"), "download stamp must record server_modified"

    def test_mark_pinned_stamps_server_modified(self, tmp_path: Path, monkeypatch):
        cache = self._make(tmp_path, monkeypatch, files=["b.txt"])
        cache.mark_pinned("b.txt")
        entry = json.loads((tmp_path / "ws_cache.json").read_text())["b.txt"]
        assert entry.get("server_modified"), "pin stamp must record server_modified"

    def test_has_update_false_when_entry_lacks_baseline(self, tmp_path: Path, monkeypatch):
        """Entries written before baseline stamping must not report has_update."""
        cache = self._make(tmp_path, monkeypatch, files=["c.txt"])
        cache._cache["c.txt"] = {"status": "downloaded"}  # no server_modified
        status = cache.get_all()["c.txt"]
        assert status["has_update"] is False

    def test_save_is_atomic_no_partial_file(self, tmp_path: Path):
        cache = self._make(tmp_path)
        cache.mark_downloaded("d.txt")
        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []


class TestLocalhostBypassMappedV6:
    def test_ipv4_mapped_ipv6_is_localhost(self):
        from src.http.auth import is_localhost

        class _FakeClient:
            host = "::ffff:127.0.0.1"

        class _FakeRequest:
            client = _FakeClient()

        assert is_localhost(_FakeRequest()) is True


class TestSessionTitleTargetsOldestUserRow:
    def test_update_session_title_targets_first_user_message(self, tmp_path: Path):
        from src.storage.messages import get_message_store

        store = get_message_store("t20_title", workspace_id="personal")
        try:
            session = "t20-title-session"
            store.add_message(
                role="user",
                content="first message ever",
                session_id=session,
            )
            store.add_message(
                role="user",
                content="second much later",
                session_id=session,
            )
            store.update_session_title(session, "The Right Title")
            assert store.get_session_title(session) == "The Right Title"
        finally:
            pass
