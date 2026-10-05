"""Installation must be explicit, isolated, and refuse unsafe destinations."""
import json
import socket
import stat
import subprocess
from pathlib import Path

import pytest

from examples.jen_reference.instance import prepare_instance, validate_instance

PACKAGE = Path(__file__).resolve().parents[2] / "examples/jen_reference"
IMAGE = "example.invalid/assistant@sha256:" + "a" * 64


def prepare(tmp_path, name="first"):
    return prepare_instance(PACKAGE, tmp_path / name, instance_id=name,
                            owner_label="synthetic-owner", engine_image=IMAGE)


@pytest.mark.parametrize("name", ["../escape", "x/y", "UPPER", "", "a" * 33, "a\nkey"])
def test_unsafe_id_leaves_no_destination(tmp_path, name):
    with pytest.raises(ValueError):
        prepare_instance(PACKAGE, tmp_path / "instance", instance_id=name,
                         owner_label="x", engine_image=IMAGE)
    assert not (tmp_path / "instance").exists()


@pytest.mark.parametrize("image", ["assistant:latest", "assistant", "x@sha256:short", "x\n@sha256:" + "a" * 64])
def test_unpinned_image_refused_before_write(tmp_path, image):
    with pytest.raises(ValueError):
        prepare_instance(PACKAGE, tmp_path / "instance", instance_id="one",
                         owner_label="x", engine_image=image)
    assert not (tmp_path / "instance").exists()


def test_existing_and_symlink_destinations_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "keep").write_text("untouched")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    for dest in (real, linked, linked / "child"):
        with pytest.raises(ValueError):
            prepare_instance(PACKAGE, dest, instance_id="one", owner_label="x", engine_image=IMAGE)
    assert (real / "keep").read_text() == "untouched"
    assert not (real / "child").exists()


def test_missing_source_or_package_symlink_refused(tmp_path):
    with pytest.raises(ValueError):
        prepare_instance(tmp_path / "absent", tmp_path / "instance", instance_id="one",
                         owner_label="x", engine_image=IMAGE)
    assert not (tmp_path / "instance").exists()
    import shutil
    copied = tmp_path / "package"
    shutil.copytree(PACKAGE, copied)
    (copied / "leak").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        prepare_instance(copied, tmp_path / "instance", instance_id="one",
                         owner_label="x", engine_image=IMAGE)
    assert not (tmp_path / "instance").exists()


def test_prepare_has_no_execution_or_network(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("preparation must not execute or connect")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    paths = prepare(tmp_path / "with spaces")
    assert validate_instance(paths) == []
    assert stat.S_IMODE(paths.env_file.stat().st_mode) == 0o600
    assert "API_KEY=" in paths.env_file.read_text()
    from src.storage.paths import DataPaths
    dp = DataPaths(user_id="default_user", data_root=str(paths.data))
    assert dp.main_agent_profile_path.read_text().startswith("---")
    assert (dp.user_tools_dir() / "fixture_store_pause/TOOL.md").is_file()


def test_instances_are_separate_and_tampering_is_reported(tmp_path):
    a, b = prepare(tmp_path, "first"), prepare(tmp_path, "second")
    assert a.env_file.read_text() != b.env_file.read_text()
    (a.data / "marker").write_text("private")
    assert not (b.data / "marker").exists()
    metadata = json.loads(a.metadata.read_text())
    assert metadata["engine_image"] == IMAGE
    assert metadata["owner_label"] == "synthetic-owner"
    (a.data / "PROFILE.md").write_text("wrong")
    assert "content_drift" in validate_instance(a)
    a.env_file.unlink()
    assert "missing_configuration" in validate_instance(a)
