"""Removed stores/config must not be recreated or existing user data erased."""

import pytest


def test_settings_expose_no_legacy_email_config():
    from src.config.settings import AppConfig
    cfg = AppConfig()
    assert not hasattr(cfg, "email")
    assert not hasattr(cfg, "email_sync")


def test_paths_expose_no_removed_store_accessors(tmp_path):
    from src.storage.paths import DataPaths
    paths = DataPaths(data_root=str(tmp_path), data_path=str(tmp_path))
    for name in ("email_dir", "gmail_cache_dir", "gmail_cache", "contacts_dir", "todos_dir",
                 "email_db", "contacts_db", "todos_db", "team_contacts_dir", "team_todos_dir"):
        assert not hasattr(paths, name), name


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_normal_server_start_does_not_create_or_modify_retired_stores(tmp_path, monkeypatch, existing):
    from src.config import reload_settings
    from src.storage import paths as paths_module
    root = tmp_path / "root"
    project = tmp_path / "project"
    original = {}
    if existing:
        for family in ("Email", "Contacts", "Todos"):
            target = root / family / "retained.db"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"operator-owned-legacy-state")
            original[target] = target.read_bytes()
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(root))
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(project))
    monkeypatch.setenv("DEPLOYMENT_MODE", "solo")
    paths_module._paths_cache.clear()
    reload_settings()
    from src.http.main import app, lifespan
    try:
        async with lifespan(app):
            for family in ("Email", "Contacts", "Todos"):
                directory = root / family
                if existing:
                    assert list(directory.iterdir()) == [directory / "retained.db"]
                else:
                    assert not directory.exists()
        for target, data in original.items():
            assert target.read_bytes() == data
    finally:
        paths_module._paths_cache.clear()
        reload_settings()
