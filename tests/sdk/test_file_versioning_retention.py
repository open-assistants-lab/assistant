"""Tests for file_versioning retention + traversal guards (audit B8/B9).

Time is frozen via a patched ``fv.datetime`` so monthly/yearly retention
buckets are deterministic regardless of the wall-clock date.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.sdk.tools_core import file_versioning as fv
from src.storage.paths import DataPaths

FIXED_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return FIXED_NOW


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the version root under tmp_path and freeze the clock."""

    def _fake_get_paths(user_id, workspace_id="personal"):
        return DataPaths(
            ea_root=str(tmp_path),
            data_path=str(tmp_path / "data"),
            user_id=user_id,
            workspace_id=workspace_id,
        )

    monkeypatch.setattr(fv, "get_paths", _fake_get_paths)
    monkeypatch.setattr(fv, "datetime", _FrozenDatetime)
    return tmp_path


def _stamp(days_ago: float) -> str:
    return (FIXED_NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H-%M-%S")


def _seed(root: Path, rel: str, stamps: list[str]) -> Path:
    ver_dir = root / rel
    ver_dir.mkdir(parents=True, exist_ok=True)
    for s in stamps:
        (ver_dir / s).write_text(f"content-{s}", encoding="utf-8")
    return ver_dir


def _clean() -> str:
    # @tool wraps the function in a ToolDefinition; invoke the raw function.
    return fv.files_versions_clean.function(user_id="u1")  # type: ignore[union-attr]


def _user_root(isolated: Path) -> Path:
    return isolated / "Users" / "u1"


class TestRetention:
    def test_monthly_bucket_keeps_only_newest(self, isolated):
        root = _user_root(isolated) / ".versions"
        # Two versions 10 days ago (inside the monthly window), 1h apart —
        # deterministic: both land in 2026-06.
        older = (FIXED_NOW - timedelta(days=10)).strftime("%Y-%m-%dT%H-%M-%S")
        newer = (FIXED_NOW - timedelta(days=10) + timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H-%M-%S"
        )
        assert newer > older
        ver_dir = _seed(root, "notes.txt", [older, newer])

        result = _clean()

        assert "Cleaned up 1" in result
        assert {p.name for p in ver_dir.iterdir()} == {newer}

    def test_recent_versions_within_seven_days_all_kept(self, isolated):
        root = _user_root(isolated) / ".versions"
        recent = [_stamp(d) for d in (1, 2, 3)]
        ver_dir = _seed(root, "log.txt", recent)

        _clean()

        assert {p.name for p in ver_dir.iterdir()} == set(recent)

    def test_yearly_bucket_keeps_only_newest(self, isolated):
        root = _user_root(isolated) / ".versions"
        # 401 and 400 days ago → both in yearly territory, same bucket.
        older, newer = _stamp(401), _stamp(400)
        ver_dir = _seed(root, "archive.txt", [older, newer])

        _clean()

        assert {p.name for p in ver_dir.iterdir()} == {newer}

    def test_regression_b8_ascending_iteration_no_longer_keeps_everything(self, isolated):
        root = _user_root(isolated) / ".versions"
        stamps = sorted(_stamp(30)[:-2] + f"{m:02d}" for m in range(0, 50, 10))
        ver_dir = _seed(root, "doc.txt", stamps)

        result = _clean()

        assert "Cleaned up 4" in result
        assert {p.name for p in ver_dir.iterdir()} == {stamps[-1]}


class TestTraversalGuards:
    def test_validate_version_accepts_wellformed_rejects_rest(self, tmp_path):
        d = tmp_path / "verdir"
        d.mkdir()
        good = _stamp(1)
        (d / good).write_text("x", encoding="utf-8")
        assert fv._validate_version(d, good) == (d / good).resolve()
        for bad in ("../../etc/passwd", "..%2F..%2Fx", "", "a/b", "20260615T120000"):
            assert fv._validate_version(d, bad) is None

    def test_restore_rejects_traversal_version(self, isolated):
        result = fv.files_versions_restore.function(  # type: ignore[union-attr]
            path="f.txt", version="../../etc/passwd", user_id="default_user"
        )
        assert "Invalid version identifier" in result

    def test_delete_rejects_traversal_version_before_existence_check(self, isolated):
        # Validation must fire even when the (traversal-derived) directory
        # does not exist — otherwise the early "No versions" return masks it.
        result = fv.files_versions_delete.function(  # type: ignore[union-attr]
            path="f.txt", version="..%2F..%2Fsecret", user_id="u1"
        )
        assert "Invalid version identifier" in result

    def test_version_path_rejects_traversal_path(self, isolated):
        with pytest.raises(ValueError, match="outside versions directory"):
            fv._version_path("u1", "../../../..")

    def test_delete_all_traversal_path_cannot_rmtree_escape(self, isolated):
        # Escape hatch probe: ../ from .versions must stay contained. (An
        # earlier draft pointed at ../../../ and literally rmtree'd pytest's
        # tmp root — live proof of audit B9.)
        victim = _user_root(isolated) / "junk-dir"
        victim.mkdir(parents=True)
        (victim / "keep.txt").write_text("x", encoding="utf-8")
        result = fv.files_versions_delete.function(  # type: ignore[union-attr]
            path="../junk-dir", version=None, user_id="u1"
        )
        assert "Error:" in result
        assert victim.exists()

    def test_list_traversal_path_returns_error_not_listing(self, isolated):
        outside = isolated / "outside"
        (outside / ".versions").mkdir(parents=True)
        (outside / ".versions" / "secret.txt").write_text("s", encoding="utf-8")
        result = fv.files_versions_list.function(path="../outside/x", user_id="default_user")  # type: ignore[union-attr]
        assert "Error:" in result
        assert "secret" not in result

    def test_restore_valid_version_still_works(self, isolated):
        root = _user_root(isolated) / ".versions"
        good = _stamp(1)
        _seed(root, "f.txt", [good])
        target_file = _user_root(isolated) / "Files" / "f.txt"

        result = fv.files_versions_restore.function(  # type: ignore[union-attr]
            path="f.txt", version=good, user_id="u1"
        )

        assert f"Restored f.txt to {good}" == result
        assert target_file.read_text(encoding="utf-8") == f"content-{good}"
