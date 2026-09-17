"""Desktop startup must own its store before changing migration/discovery state."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.http import desktop


@pytest.fixture
def startup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path / ".system"))
    monkeypatch.setenv("DESKTOP_LAUNCH_TOKEN", "test-launch-token")
    monkeypatch.setattr(desktop, "apply_desktop_settings", lambda: None)
    return tmp_path


def test_losing_cli_launch_preserves_owner_state(startup_env, monkeypatch):
    root = startup_env
    owner = desktop.acquire_sidecar_lock()
    assert owner is not None
    system = root / ".system"
    rendezvous = system / desktop.RENDZVOUS_FILE
    rendezvous.write_text(json.dumps({"pid": 123, "nonce": "owner-nonce"}))
    before = rendezvous.read_bytes()
    lock_before = (system / desktop.LOCK_FILE).read_bytes()
    migration = Mock()
    monkeypatch.setattr("src.storage.desktop_migration.run_desktop_migration", migration)
    try:
        with pytest.raises(SystemExit):
            desktop.desktop_main()
        migration.assert_not_called()
        assert rendezvous.read_bytes() == before
        assert (system / desktop.LOCK_FILE).read_bytes() == lock_before
    finally:
        desktop.release_sidecar_lock(owner)


def test_cli_holds_one_lock_through_migration_and_serving(startup_env, monkeypatch):
    stages = []

    def migrate(root: Path) -> None:
        assert desktop.acquire_sidecar_lock() is None
        stages.append("migration")

    def serve(**kwargs) -> None:
        assert desktop.acquire_sidecar_lock() is None
        stages.append("serve")

    monkeypatch.setattr("src.storage.desktop_migration.run_desktop_migration", migrate)
    monkeypatch.setattr(desktop, "run_desktop_server", serve)
    desktop.desktop_main()
    assert stages == ["migration", "serve"]
    released = desktop.acquire_sidecar_lock()
    assert released is not None
    desktop.release_sidecar_lock(released)


def test_cli_releases_lock_after_migration_recovery(startup_env, monkeypatch):
    def migrate(root: Path) -> None:
        raise SystemExit(1)

    monkeypatch.setattr("src.storage.desktop_migration.run_desktop_migration", migrate)
    with pytest.raises(SystemExit):
        desktop.desktop_main()
    released = desktop.acquire_sidecar_lock()
    assert released is not None
    desktop.release_sidecar_lock(released)
