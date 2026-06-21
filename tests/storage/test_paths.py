"""Tests for deployment-aware data paths."""

from __future__ import annotations

import pytest

from src.storage.paths import DataPaths


@pytest.mark.parametrize("user_id", ["alice/../bob", "alice:bob"])
def test_user_id_rejects_aliasing_and_separators(tmp_path, user_id):
    with pytest.raises(ValueError, match="Invalid user_id"):
        DataPaths(data_path=str(tmp_path / "data"), user_id=user_id)


def test_user_dir_rejects_traversal_user_id(tmp_path):
    with pytest.raises(ValueError, match="Invalid user_id"):
        DataPaths(ea_root=str(tmp_path / "data"), user_id="../../escaped")

    assert not (tmp_path / "escaped").exists()


def test_user_dir_accepts_normal_user_ids(tmp_path):
    for user_id in ("default_user", "alice_test"):
        paths = DataPaths(ea_root=str(tmp_path / "data"), user_id=user_id)

        user_dir = paths.user_dir

        assert user_dir == tmp_path / "data"
        assert user_dir.exists()
