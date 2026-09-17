"""Desktop startup must own its store before changing migration/discovery state."""

import json
import threading
import time
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


def _wait_rendezvous(system_dir: Path, timeout: float = 20.0) -> dict:
    path = system_dir / "rendezvous.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return json.loads(path.read_text())
        time.sleep(0.05)
    raise AssertionError("rendezvous.json not written before timeout")


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


def test_serving_never_releases_borrowed_lock(startup_env):
    """run_desktop_server must not unlock a borrowed (caller-owned) lock.

    Review P2: the old code executed flock(LOCK_UN) unconditionally at the
    end of serving, so a borrowed lock was released before desktop_main()
    (the owner) released it — a second launch could acquire mid-shutdown.
    Runs the REAL serving body (no stubs): start, wait for readiness, stop,
    then verify the owner still holds the lock.
    """
    system_dir = startup_env / ".system"
    lock = desktop.acquire_sidecar_lock()
    assert lock is not None
    stop = threading.Event()
    outcome: dict[str, BaseException | None] = {}

    def run() -> None:
        try:
            desktop.run_desktop_server(stop_event=stop, lock=lock)
            outcome["err"] = None
        except BaseException as e:  # noqa: BLE001 - test harness record
            outcome["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        _wait_rendezvous(system_dir)  # readiness gate: server fully started
        stop.set()
        t.join(timeout=15)
        assert outcome["err"] is None, f"serving raised: {outcome['err']}"
        # On the old code this reacquire SUCCEEDS (serving unlocked the
        # borrowed lock at shutdown) and the ownership contract is broken.
        assert desktop.acquire_sidecar_lock() is None, (
            "run_desktop_server released a caller-owned lock"
        )
    finally:
        stop.set()
        t.join(timeout=5)
        desktop.release_sidecar_lock(lock)
    # After the owner releases, the next launch can acquire.
    reacquired = desktop.acquire_sidecar_lock()
    assert reacquired is not None
    desktop.release_sidecar_lock(reacquired)


def test_run_desktop_server_releases_self_acquired_lock(startup_env):
    """Direct-call path: an internally acquired lock is released by serving.

    Real serving body, no lock passed: run_desktop_server acquires its own
    lock and must release it on return so a subsequent acquirer succeeds.
    """
    system_dir = startup_env / ".system"
    stop = threading.Event()
    outcome: dict[str, BaseException | None] = {}

    def run() -> None:
        try:
            desktop.run_desktop_server(stop_event=stop)
            outcome["err"] = None
        except BaseException as e:  # noqa: BLE001 - test harness record
            outcome["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        _wait_rendezvous(system_dir)
        stop.set()
        t.join(timeout=15)
        assert outcome["err"] is None, f"serving raised: {outcome['err']}"
    finally:
        stop.set()
        t.join(timeout=5)
    reacquired = desktop.acquire_sidecar_lock()
    assert reacquired is not None
    desktop.release_sidecar_lock(reacquired)
