"""Desktop sidecar mode (desktop v0.1, Phase D1).

`assistant desktop-server` runs the API as a local product sidecar:

- binds 127.0.0.1 ONLY on an OS-assigned dynamic port
- single-instance lock (flock + recorded pid; never touches a process it
  does not own)
- launch-nonce rendezvous record written ONLY after server readiness
- token-protected local API: SOLO_BYPASS is disabled in this mode — the
  launch token IS the auth (D0 decision)
- server-side identity: every request resolves to `default_user`; the
  native client never chooses user_id/workspace
- applies the desktop data roots and the one-source storage migration
  before stores initialize
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import IO

from src.app_logging import get_logger

logger = get_logger()

RENDZVOUS_FILE = "rendezvous.json"
LOCK_FILE = "sidecar.lock"

_lock_holder = None


def acquire_sidecar_lock() -> IO[str] | None:
    """Acquire the single-instance sidecar lock (flock).

    Returns the lock file handle on success (the caller holds it for the
    process lifetime) or None when another live sidecar owns the lock. This
    implementation never attaches to or kills a process it does not own: the
    recorded pid is informational only.
    """
    import fcntl

    system_dir = Path(os.environ["DEPLOYMENT_DATA_PATH"])
    system_dir.mkdir(parents=True, exist_ok=True)
    lock_path = system_dir / LOCK_FILE
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        fh.close()
        return None
    fh.write(json.dumps({"pid": os.getpid(), "ts": time.time()}))
    fh.flush()
    os.fsync(fh.fileno())
    global _lock_holder
    _lock_holder = fh  # the lock must survive the caller's handle scope
    return fh


def release_sidecar_lock(lock: IO[str]) -> None:
    import fcntl

    try:
        fcntl.flock(lock, fcntl.LOCK_UN)
    except (OSError, ValueError):
        pass
    lock.close()
    global _lock_holder
    _lock_holder = None


def _prebind_loopback_sock() -> tuple[socket.socket, int]:
    """Bind 127.0.0.1 on an OS-assigned port; return the socket + port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen(64)
    return s, port


def sidecar_versions() -> dict[str, str]:
    """Version tuple for the rendezvous + bootstrap payloads."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        app_version = version("assistant")
    except PackageNotFoundError:
        app_version = "dev"
    return {
        "app": app_version,
        "api": "v1",
        "stream_protocol": "v1-block-stream",
        # agent-browser + browser runtime versions are pinned at release
        # assembly (D5); the sidecar reports what is configured today.
        "agent_browser": os.environ.get("DESKTOP_AGENT_BROWSER_VERSION", "unpinned"),
        "browser_runtime": os.environ.get(
            "DESKTOP_BROWSER_RUNTIME_VERSION", "unpinned"
        ),
    }


def _write_rendezvous(system_dir: Path, rendezvous: dict[str, object]) -> None:
    """Atomically publish non-secret sidecar discovery metadata."""
    target = system_dir / RENDZVOUS_FILE
    temporary = system_dir / f".{RENDZVOUS_FILE}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "x", encoding="utf-8") as handle:
            handle.write(json.dumps(rendezvous, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def run_desktop_server(stop_event: threading.Event | None = None) -> None:
    """Run the desktop sidecar: lock, migrate, serve, write rendezvous.

    Blocks until `stop_event` is set (tests) or the process is terminated.
    """
    import fcntl

    from src.config import reload_settings

    # Desktop data roots are applied by the caller (the CLI command) BEFORE
    # any store initializes; reload so settings see them.
    reload_settings()

    lock = acquire_sidecar_lock()
    if lock is None:
        logger.error("desktop.sidecar_already_running", {})
        print(
            "A sidecar instance is already running for this data root "
            f"({Path(os.environ['DEPLOYMENT_DATA_PATH'])}).",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        # The native launcher generates and retains this token, then passes it
        # only to its child sidecar. Persisting a generated fallback in the
        # rendezvous file would let any same-user process impersonate the app.
        token = os.environ.get("DESKTOP_LAUNCH_TOKEN")
        if not token:
            logger.error("desktop.sidecar_missing_launch_token", {})
            print("Desktop sidecar requires DESKTOP_LAUNCH_TOKEN.", file=sys.stderr)
            sys.exit(1)
        from src.http import auth as auth_mod

        auth_mod.set_desktop_launch_token(token)

        sock, port = _prebind_loopback_sock()

        import uvicorn

        from src.http.main import app

        config = uvicorn.Config(
            app,
            log_level="warning",
            access_log=False,
            fd=sock.fileno(),
        )
        server = uvicorn.Server(config)

        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        deadline = time.time() + 30
        while time.time() < deadline and not getattr(server, "started", False):
            if stop_event is not None and stop_event.is_set():
                break
            time.sleep(0.05)

        if not getattr(server, "started", False):
            logger.error("desktop.sidecar_start_timeout", {})
            sys.exit(1)

        # Readiness gate passed — NOW write the rendezvous record.
        system_dir = Path(os.environ["DEPLOYMENT_DATA_PATH"])
        rendezvous = {
            "host": "127.0.0.1",
            "port": port,
            "pid": os.getpid(),
            "nonce": uuid.uuid4().hex,
            "started_at": time.time(),
            "versions": sidecar_versions(),
        }
        _write_rendezvous(system_dir, rendezvous)
        from src.config import get_settings

        logger.info(
            "desktop.sidecar_ready",
            {"port": port, "data_root": get_settings().deployment.data_root},
        )

        while not (stop_event is not None and stop_event.is_set()) and not (
            server.should_exit
        ):
            time.sleep(0.2)
        server.should_exit = True
        thread.join(timeout=10)
        try:
            (system_dir / RENDZVOUS_FILE).unlink()
        except FileNotFoundError:
            pass
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
    finally:
        release_sidecar_lock(lock)

def desktop_main() -> None:
    """CLI entry: assistant desktop-server.

    Applies the desktop data roots and the one-source storage migration
    BEFORE stores initialize, then serves the sidecar until exit.
    """
    home = Path.home() / "Assistant"
    # Product-sidecar configuration is not caller-configurable: .env values
    # for developer/server modes must not redirect user data or bypass launch
    # token auth in an installed desktop app.
    os.environ["DEPLOYMENT_MODE"] = "desktop-server"
    os.environ["DEPLOYMENT_DATA_ROOT"] = str(home)
    os.environ["DEPLOYMENT_DATA_PATH"] = str(home / ".system")
    os.environ["SOLO_BYPASS"] = "false"

    from src.config import reload_settings

    reload_settings()

    if not os.environ.get("DESKTOP_LAUNCH_TOKEN"):
        logger.error("desktop.sidecar_missing_launch_token", {})
        print("Desktop sidecar requires DESKTOP_LAUNCH_TOKEN.", file=sys.stderr)
        sys.exit(1)

    # One-source storage migration BEFORE any store initializes (D1 task 4).
    from src.storage.desktop_migration import run_desktop_migration

    run_desktop_migration(Path(os.environ["DEPLOYMENT_DATA_ROOT"]))

    # Never serve with a stale rendezvous from a previous run.
    system_dir = Path(os.environ["DEPLOYMENT_DATA_PATH"])
    system_dir.mkdir(parents=True, exist_ok=True)
    try:
        (system_dir / RENDZVOUS_FILE).unlink()
    except FileNotFoundError:
        pass

    run_desktop_server()
