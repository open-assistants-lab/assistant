"""Desktop v0.1 Phase D1: sidecar mode, storage, identity, capability baseline.

Covers the D1 exit-gate items: dynamic loopback binding, required bearer
token, nonce ownership, second-launch safety, migration branches, excluded
tool families, and the authenticated bootstrap endpoint.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time

from pathlib import Path

import pytest


@pytest.fixture()
def desktop_env(tmp_path, monkeypatch):
    """Isolated desktop environment: temp data root, desktop-server mode."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DEPLOYMENT_MODE", "desktop-server")
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(home / "Assistant"))
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(home / "Assistant" / ".system"))
    monkeypatch.setenv("SOLO_BYPASS", "false")
    monkeypatch.delenv("API_KEY", raising=False)
    from src.config import settings as settings_module

    settings_module._config = None
    yield home
    settings_module._config = None


def _wait_rendezvous(system_dir: Path, timeout: float = 20.0) -> dict:
    path = system_dir = Path(os.environ["DEPLOYMENT_DATA_PATH"]) / "rendezvous.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return json.loads(path.read_text())
        time.sleep(0.05)
    raise AssertionError("rendezvous file never appeared")


def _stop_server(server_holder):
    server_holder["server"].should_exit = True
    server_holder["thread"].join(timeout=10)


# --------------------------------------------------------------------------
# Task 2: desktop-server mode — dynamic loopback binding + token auth
# --------------------------------------------------------------------------


class TestDesktopServerMode:
    def test_binds_loopback_dynamic_port_and_writes_rendezvous_after_ready(
        self, desktop_env, tmp_path
    ):
        """Dynamic OS-assigned port on 127.0.0.1; rendezvous only after
        readiness; record carries host/port/token/pid."""
        from src.http.desktop import run_desktop_server

        holder: dict[str, object] = {}
        stop = threading.Event()
        t = threading.Thread(
            target=lambda: holder.update(run=run_desktop_server(stop_event=stop)),
            daemon=True,
        )
        t.start()
        try:
            rd = _wait_rendezvous(Path(os.environ["DEPLOYMENT_DATA_PATH"]))
            assert rd["host"] == "127.0.0.1"
            assert isinstance(rd["port"], int) and 1024 < rd["port"] < 65536
            assert rd["token"]
            assert rd["pid"] == os.getpid()

            # Loopback-only: the API answers on the recorded port with the token.
            import urllib.request

            req = urllib.request.Request(
                f"http://127.0.0.1:{rd['port']}/v1/bootstrap",
                headers={"Authorization": f"Bearer {rd['token']}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read())
            assert body["identity"]["user_id"] == "default_user"
        finally:
            stop.set()
            t.join(timeout=15)

    def test_bearer_token_required(self, desktop_env, tmp_path):
        from src.http.desktop import run_desktop_server

        stop = threading.Event()
        t = threading.Thread(
            target=lambda: run_desktop_server(stop_event=stop), daemon=True
        )
        t.start()
        try:
            rd = _wait_rendezvous(Path(os.environ["DEPLOYMENT_DATA_PATH"]).parent)
            import urllib.error
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{rd['port']}/v1/bootstrap")
            try:
                urllib.request.urlopen(req, timeout=10)
                raise AssertionError("expected 401 without token")
            except urllib.error.HTTPError as e:
                assert e.code == 401
            req = urllib.request.Request(
                f"http://127.0.0.1:{rd['port']}/v1/bootstrap",
                headers={"Authorization": "Bearer wrong-token"},
            )
            try:
                urllib.request.urlopen(req, timeout=10)
                raise AssertionError("expected 401 with wrong token")
            except urllib.error.HTTPError as e:
                assert e.code == 401
        finally:
            stop.set()
            t.join(timeout=15)

    def test_second_launch_exits_when_lock_held(self, desktop_env, tmp_path):
        """Single-instance lock: a live holder wins; the loser exits nonzero."""
        import subprocess
        import sys

        # Occupy the lock from THIS process via the same helper.
        from src.http.desktop import acquire_sidecar_lock

        lock = acquire_sidecar_lock()
        assert lock is not None

        # A second process must fail to start.
        code = (
            "import os,sys;"
            "os.environ.setdefault('DEPLOYMENT_MODE','desktop-server');"
            f"os.environ['DEPLOYMENT_DATA_ROOT']={str(tmp_path / 'home' / 'Assistant')!r};"
            "from src.http.desktop import acquire_sidecar_lock;"
            "sys.exit(3 if acquire_sidecar_lock() is None else 0)"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True)
        assert out.returncode == 3

        import fcntl

        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


# --------------------------------------------------------------------------
# Task 3: server-side default_user identity
# --------------------------------------------------------------------------


class TestDesktopIdentity:
    @pytest.mark.asyncio
    async def test_client_user_id_is_ignored_in_desktop_mode(self, desktop_env):
        """The server resolves default_user even when a client sends another."""
        import httpx

        from src.http.auth import DesktopTokenResolver
        from src.http.main import app

        token = "test-launch-token"
        os.environ["DESKTOP_LAUNCH_TOKEN"] = token
        try:
            from src.config import reload_settings

            reload_settings()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1",
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                resp = await client.get(
                    "/v1/conversation/sessions", params={"user_id": "native_sdk_chat"}
                )
                assert resp.status_code == 200
            # And the resolver itself scopes to default_user.
            class FakeRequest:
                headers = {"authorization": f"Bearer {token}"}
                client = None

            ident = DesktopTokenResolver({token}).resolve(FakeRequest())  # type: ignore[arg-type]
            assert ident is not None and ident.user_id == "default_user"
        finally:
            os.environ.pop("DESKTOP_LAUNCH_TOKEN", None)


# --------------------------------------------------------------------------
# Task 4: storage migration (one source; dual-tree recovery)
# --------------------------------------------------------------------------


class TestDesktopMigration:
    def _system(self, home: Path) -> Path:
        return home / "Assistant" / ".system"

    def _run_mig(self, home: Path):
        from src.storage.desktop_migration import run_desktop_migration

        return run_desktop_migration(home / "Assistant", self._system(home))

    def test_fresh_install_writes_marker_only(self, tmp_path):
        from src.storage.desktop_migration import run_desktop_migration

        home = tmp_path / "home"
        out = self._run_mig(home)
        assert (self._system(home) / "migration-complete.json").exists()
        assert out["source"] == "fresh"

    def test_conversation_tree_renamed_to_messages(self, tmp_path):
        from src.storage.desktop_migration import run_desktop_migration

        home = tmp_path / "home"
        conv = home / "Assistant" / "Conversation"
        conv.mkdir(parents=True)
        (conv / "app.db").write_bytes(b"sqlite")
        out = self._run_mig(home)
        assert out["source"] == "conversation"
        assert (home / "Assistant" / "Messages" / "app.db").exists()
        assert not conv.exists()

    def test_native_sdk_chat_promoted(self, tmp_path):
        from src.storage.desktop_migration import run_desktop_migration

        home = tmp_path / "home"
        legacy = home / "Assistant" / "Users" / "native_sdk_chat"
        (legacy / "Conversation").mkdir(parents=True)
        (legacy / "Conversation" / "app.db").write_bytes(b"sqlite")
        (legacy / "Files").mkdir()
        out = self._run_mig(home)
        assert out["source"] == "native_sdk_chat"
        root = home / "Assistant"
        assert (root / "Messages" / "app.db").exists()
        assert (root / "Files").exists()
        assert not legacy.exists()

    def test_dual_tree_recovery_stop(self, tmp_path):
        """Both sources -> explicit recovery state, no destructive action."""
        from src.storage.desktop_migration import run_desktop_migration

        home = tmp_path / "home"
        (home / "Assistant" / "Conversation").mkdir(parents=True)
        legacy = home / "Assistant" / "Users" / "native_sdk_chat"
        legacy.mkdir(parents=True)
        with pytest.raises(SystemExit):
            self._run_mig(home)
        recovery = self._system(home) / "migration-recovery.json"
        assert recovery.exists()
        state = json.loads(recovery.read_text())
        assert state["requires_recovery"] is True
        # Nothing moved.
        assert (home / "Assistant" / "Conversation").exists()
        assert legacy.exists()

    def test_idempotent_marker_short_circuits(self, tmp_path):
        home = tmp_path / "home"
        first = self._run_mig(home)
        conv = home / "Assistant" / "Conversation"
        conv.mkdir(parents=True)  # appeared after migration — must be ignored
        out = self._run_mig(home)
        assert first["source"] == "fresh"
        assert out["source"] == "already-migrated"
        assert conv.exists()


# --------------------------------------------------------------------------
# Task 5: capability filtering before registration
# --------------------------------------------------------------------------


class TestDesktopCapabilityFiltering:
    def test_excluded_tool_families_absent(self, desktop_env):
        from src.config import reload_settings
        from src.sdk import native_tools

        reload_settings()
        native_tools.reset_native_tools()
        try:
            names = [t.name for t in native_tools.get_native_tools()]
            assert not [n for n in names if n.startswith("email_")]
            assert not [n for n in names if n.startswith("contacts_")]
            assert not [n for n in names if n.startswith("todos_")]
            # Retained: shell + browser + core
            assert "shell_execute" in names
            assert "browser_open" in names
        finally:
            os.environ.pop("DEPLOYMENT_MODE", None)
            reload_settings()
            native_tools.reset_native_tools()

    def test_dev_router_stripped_in_desktop_mode(self, desktop_env):
        import importlib

        from src.config import reload_settings
        import src.http.main as main_mod

        reload_settings()
        # Mode-dependent mounting happens at app creation — rebuild the app
        # under desktop settings (the module-level app in main was created
        # under whatever settings were live at import time).
        importlib.reload(main_mod)
        try:
            paths = {r.path for r in main_mod.app.routes}
            assert not any(p.startswith("/dev") for p in paths)
        finally:
            importlib.reload(main_mod)


# --------------------------------------------------------------------------
# Task 6: bootstrap endpoint
# --------------------------------------------------------------------------


class TestBootstrapEndpoint:
    @pytest.mark.asyncio
    async def test_bootstrap_shape_and_auth(self, desktop_env):
        import httpx

        from src.http.main import app

        token = "test-launch-token"
        os.environ["DESKTOP_LAUNCH_TOKEN"] = token
        try:
            from src.config import reload_settings

            reload_settings()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1",
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                resp = await client.get("/v1/bootstrap")
                assert resp.status_code == 200
                body = resp.json()
                for key in (
                    "versions",
                    "capability_profile",
                    "migration",
                    "identity",
                    "sidecar",
                ):
                    assert key in body
                assert body["identity"]["user_id"] == "default_user"
                assert body["capability_profile"] == "desktop-v0.1.0"
                assert "agent_browser" in body["versions"]

                # Without token -> 401 (separate headerless client — the
                # authed client would inject its default header).
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://127.0.0.1"
                ) as anon:
                    unauth = await anon.get("/v1/bootstrap")
                assert unauth.status_code == 401
        finally:
            os.environ.pop("DESKTOP_LAUNCH_TOKEN", None)
            from src.config import reload_settings

            reload_settings()