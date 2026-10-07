"""Offline verification copies only declared build packages, not operator caches/data."""

import pytest


def test_cached_build_copy_rebinds_archive_links_and_preserves_source(tmp_path):
    from scripts.verify_refactor import copy_cached_build_dependencies

    source = tmp_path / "operator-cache"
    archive = source / "archive-v0" / "cached-hatchling"
    archive.mkdir(parents=True)
    (archive / "package.py").write_text("cached build package")
    wheel = source / "wheels-v5" / "pypi" / "hatchling"
    wheel.mkdir(parents=True)
    (wheel / "1.0-py3-none-any").symlink_to(archive, target_is_directory=True)
    (wheel / "1.0-py3-none-any.msgpack").write_bytes(b"cached-index")
    unrelated = source / "wheels-v5" / "pypi" / "private-package"
    unrelated.mkdir()
    (unrelated / "private.txt").write_text("private sentinel")
    index = source / "simple-v18" / "pypi"
    index.mkdir(parents=True)
    (index / "hatchling.rkyv").write_bytes(b"cached-version-index")
    (index / "private-package.rkyv").write_bytes(b"unrelated private index")
    destination = tmp_path / "isolated-cache"
    copy_cached_build_dependencies(source, destination)
    copied_link = destination / "wheels-v5/pypi/hatchling/1.0-py3-none-any"
    assert copied_link.resolve().is_relative_to(destination)
    assert (destination / "simple-v18/pypi/hatchling.rkyv").read_bytes() == b"cached-version-index"
    assert not (destination / "simple-v18/pypi/private-package.rkyv").exists()
    assert (copied_link / "package.py").read_text() == "cached build package"
    assert not (destination / "wheels-v5/pypi/private-package").exists()
    assert (wheel / "1.0-py3-none-any").resolve() == archive
    assert (wheel / "1.0-py3-none-any.msgpack").read_bytes() == b"cached-index"


def test_cache_copy_refuses_reference_outside_cached_archives(tmp_path):
    from scripts.verify_refactor import copy_cached_build_dependencies

    source = tmp_path / "cache"
    wheel = source / "wheels-v5/pypi/hatchling"
    wheel.mkdir(parents=True)
    outside = tmp_path / "operator-private"
    outside.mkdir()
    (outside / "secret").write_text("not build dependencies")
    (wheel / "bad-version").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        copy_cached_build_dependencies(source, tmp_path / "destination")
