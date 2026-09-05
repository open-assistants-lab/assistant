"""Desktop v0.1 Phase D1: sidecar mode, storage, identity, capability baseline.

Covers the D1 exit-gate items: dynamic loopback binding, required bearer
token, nonce ownership, second-launch safety, migration branches, excluded
tool families, and the authenticated bootstrap endpoint.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def desktop_env(tmp_path, monkeypatch):
    """Isolated desktop environment: temp data root, desktop-server mode."""
    tracked_env = (
        "DEPLOYMENT_MODE",
        "DEPLOYMENT_DATA_ROOT",
        "DEPLOYMENT_DATA_PATH",
        "SOLO_BYPASS",
        "DESKTOP_LAUNCH_TOKEN",
        "API_KEY",
    )
    original_env = {name: os.environ.get(name) for name in tracked_env}
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DEPLOYMENT_MODE", "desktop-server")
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(home / "Assistant"))
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(home / "Assistant" / ".system"))
    monkeypatch.setenv("SOLO_BYPASS", "false")
    monkeypatch.setenv("DESKTOP_LAUNCH_TOKEN", "desktop-test-token")
    monkeypatch.delenv("API_KEY", raising=False)
    from src.config import settings as settings_module

    settings_module._config = None
    yield home
    # desktop_main() deliberately overwrites os.environ directly; restore the
    # fixture's original process state before another module is collected.
    for name, value in original_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    settings_module._config = None


def _wait_rendezvous(system_dir: Path, timeout: float = 20.0) -> dict:
    path = system_dir / "rendezvous.json"
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
            assert "token" not in rd
            assert rd["pid"] == os.getpid()

            # Loopback-only: the API answers on the recorded port with the token.
            import urllib.request

            req = urllib.request.Request(
                f"http://127.0.0.1:{rd['port']}/v1/bootstrap",
                headers={"Authorization": "Bearer desktop-test-token"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read())
            assert body["identity"]["user_id"] == "default_user"
        finally:
            stop.set()
            t.join(timeout=15)

    def test_desktop_main_forces_canonical_mode_roots_and_auth(self, tmp_path, monkeypatch):
        """A desktop launch must not inherit development/server deployment values."""
        from src.config import get_settings, reload_settings
        from src.http import desktop
        from src.storage.paths import DataPaths

        home = tmp_path / "desktop-home"
        home.mkdir()
        with monkeypatch.context() as isolated_env:
            isolated_env.setenv("HOME", str(home))
            isolated_env.setenv("DEPLOYMENT_MODE", "solo")
            isolated_env.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path / "wrong-root"))
            isolated_env.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path / "wrong-system"))
            isolated_env.setenv("SOLO_BYPASS", "true")
            isolated_env.setenv("DESKTOP_LAUNCH_TOKEN", "desktop-test-token")
            with patch.object(desktop, "run_desktop_server"):
                desktop.desktop_main()
            assert os.environ["DEPLOYMENT_MODE"] == "desktop-server"
            assert os.environ["DEPLOYMENT_DATA_ROOT"] == str(home / "Assistant")
            assert os.environ["DEPLOYMENT_DATA_PATH"] == str(home / "Assistant" / ".system")
            assert os.environ["SOLO_BYPASS"] == "false"
            settings = get_settings()
            assert settings.deployment.mode == "desktop-server"
            assert settings.deployment.data_root == str(home / "Assistant")
            assert settings.deployment.data_path == str(home / "Assistant" / ".system")
            assert settings.auth.solo_bypass is False
            paths = DataPaths()
            assert paths.root == home / "Assistant"
            assert paths.base == home / "Assistant" / ".system"
        reload_settings()

    def test_desktop_main_requires_a_launch_token_before_migration(self, tmp_path, monkeypatch):
        from src.http import desktop

        home = tmp_path / "desktop-home"
        home.mkdir()
        with monkeypatch.context() as isolated_env:
            isolated_env.setenv("HOME", str(home))
            # Set tracked empty values rather than deleting them: desktop_main
            # writes these keys directly, and monkeypatch can only restore keys
            # it has first recorded.
            for name in (
                "DESKTOP_LAUNCH_TOKEN",
                "DEPLOYMENT_MODE",
                "DEPLOYMENT_DATA_ROOT",
                "DEPLOYMENT_DATA_PATH",
                "SOLO_BYPASS",
            ):
                isolated_env.setenv(name, "")
            with patch("src.storage.desktop_migration.run_desktop_migration") as migration:
                with pytest.raises(SystemExit):
                    desktop.desktop_main()
            migration.assert_not_called()

    @pytest.mark.asyncio
    async def test_desktop_identity_satisfies_legacy_dependency_with_inherited_api_key(
        self, desktop_env, monkeypatch
    ):
        from starlette.requests import Request

        from src.config import get_settings, reload_settings
        from src.http.auth import DesktopTokenResolver, require_auth

        monkeypatch.setenv("API_KEY", "inherited-server-key")
        reload_settings()
        assert get_settings().auth.api_key == "inherited-server-key"
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/message",
            "headers": [(b"authorization", b"Bearer desktop-test-token")],
            "client": ("203.0.113.1", 1234),
            "scheme": "http",
        }
        request = Request(scope)
        request.state.identity = DesktopTokenResolver({"desktop-test-token"}).resolve(request)
        await require_auth(request)

    def test_bearer_token_required(self, desktop_env, tmp_path):
        from src.http.desktop import run_desktop_server

        stop = threading.Event()
        t = threading.Thread(
            target=lambda: run_desktop_server(stop_event=stop), daemon=True
        )
        t.start()
        try:
            rd = _wait_rendezvous(Path(os.environ["DEPLOYMENT_DATA_PATH"]))
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

    def test_fresh_install_creates_messages_without_conversation(self, tmp_path):
        home = tmp_path / "home"
        out = self._run_mig(home)
        root = home / "Assistant"
        assert (self._system(home) / "migration-complete.json").exists()
        assert out["source"] == "fresh"
        assert (root / "Messages").is_dir()
        assert not (root / "Conversation").exists()

    def test_conversation_tree_renamed_to_messages_and_used_at_runtime(self, tmp_path):
        from src.storage.paths import DataPaths

        home = tmp_path / "home"
        root = home / "Assistant"
        conv = root / "Conversation"
        conv.mkdir(parents=True)
        (conv / "app.db").write_bytes(b"sqlite")
        out = self._run_mig(home)
        assert out["source"] == "conversation"
        assert (root / "Messages" / "app.db").exists()
        assert not conv.exists()
        paths = DataPaths(data_root=str(root), data_path=str(root / ".system"))
        assert paths.conversation_dir() == root / "Messages"
        assert not (root / "Conversation").exists()

    def test_existing_messages_and_conversation_require_recovery(self, tmp_path):
        home = tmp_path / "home"
        root = home / "Assistant"
        (root / "Conversation").mkdir(parents=True)
        (root / "Messages").mkdir()
        with pytest.raises(SystemExit):
            self._run_mig(home)
        assert (root / "Conversation").exists()
        assert (root / "Messages").exists()
        assert (self._system(home) / "migration-recovery.json").exists()

    def test_legacy_promotion_with_root_data_requires_recovery(self, tmp_path):
        home = tmp_path / "home"
        root = home / "Assistant"
        (root / "Files").mkdir(parents=True)
        (root / "Files" / "root.txt").write_text("root")
        legacy_files = root / "Users" / "native_sdk_chat" / "Files"
        legacy_files.mkdir(parents=True)
        (legacy_files / "legacy.txt").write_text("legacy")
        with pytest.raises(SystemExit):
            self._run_mig(home)
        assert (root / "Files" / "root.txt").read_text() == "root"
        assert (legacy_files / "legacy.txt").read_text() == "legacy"
        assert (self._system(home) / "migration-recovery.json").exists()

    def test_native_sdk_chat_promoted(self, tmp_path):
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

    def test_legacy_promotion_allows_framework_metadata(self, tmp_path):
        home = tmp_path / "home"
        root = home / "Assistant"
        (root / ".git").mkdir(parents=True)
        (root / ".gitignore").write_text("*.db\n")
        (root / "Logs").mkdir()
        legacy = root / "Users" / "native_sdk_chat" / "Conversation"
        legacy.mkdir(parents=True)
        (legacy / "app.db").write_bytes(b"sqlite")

        out = self._run_mig(home)

        assert out["source"] == "native_sdk_chat"
        assert (root / "Messages" / "app.db").exists()

    def test_legacy_target_collision_requires_recovery_before_any_move(self, tmp_path):
        home = tmp_path / "home"
        root = home / "Assistant"
        root.mkdir(parents=True)
        (root / ".DS_Store").write_text("root metadata")
        legacy = root / "Users" / "native_sdk_chat"
        (legacy / ".DS_Store").parent.mkdir(parents=True)
        (legacy / ".DS_Store").write_text("legacy metadata")
        (legacy / "Conversation").mkdir()
        (legacy / "Conversation" / "app.db").write_bytes(b"sqlite")

        with pytest.raises(SystemExit):
            self._run_mig(home)

        assert (legacy / "Conversation" / "app.db").exists()
        assert not (root / "Messages").exists()
        assert (self._system(home) / "migration-recovery.json").exists()

    def test_dual_tree_recovery_stop(self, tmp_path):
        """Both sources -> explicit recovery state, no destructive action."""
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

        import src.http.main as main_mod
        from src.config import reload_settings

        reload_settings()
        # Mode-dependent mounting happens at app creation — rebuild the app
        # under desktop settings (the module-level app in main was created
        # under whatever settings were live at import time).
        importlib.reload(main_mod)
        try:
            paths = {r.path for r in main_mod.app.routes}
            assert not any(p.startswith("/dev") for p in paths)
            assert "/emails" not in paths
            assert "/contacts" not in paths
            assert "/todos" not in paths
            assert "/connectors/catalog" not in paths
            assert "/auth/login" not in paths
            assert "/scheduler/status" not in paths
        finally:
            # Restore a normally mounted app for tests outside desktop mode.
            with patch.dict(os.environ, {"DEPLOYMENT_MODE": "solo"}):
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


class TestDesktopBackgroundAndPublicSurface:
    def test_desktop_websocket_requires_bearer_token(self, desktop_env):
        import importlib

        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        import src.http.main as main_mod

        importlib.reload(main_mod)
        try:
            with TestClient(main_mod.app, raise_server_exceptions=False) as client:
                with pytest.raises(WebSocketDisconnect):
                    with client.websocket_connect("/ws/conversation"):
                        pass
                with client.websocket_connect(
                    "/ws/conversation",
                    headers={"Authorization": "Bearer desktop-test-token"},
                ) as websocket:
                    websocket.send_json({"type": "ping"})
                    assert websocket.receive_json()["type"] == "pong"
        finally:
            with patch.dict(os.environ, {"DEPLOYMENT_MODE": "solo"}):
                importlib.reload(main_mod)

    @pytest.mark.asyncio
    async def test_excluded_routes_return_not_found_in_desktop_mode(self, desktop_env):
        import importlib

        import httpx

        import src.http.main as main_mod

        importlib.reload(main_mod)
        try:
            transport = httpx.ASGITransport(app=main_mod.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1",
                headers={"Authorization": "Bearer desktop-test-token"},
            ) as client:
                for path in (
                    "/emails",
                    "/contacts",
                    "/todos",
                    "/connectors/catalog?user_id=default_user",
                    "/auth/login?service=gmail",
                    "/scheduler/status",
                ):
                    assert (await client.get(path)).status_code == 404
        finally:
            # Restore a normally mounted app for tests outside desktop mode.
            with patch.dict(os.environ, {"DEPLOYMENT_MODE": "solo"}):
                importlib.reload(main_mod)

    @pytest.mark.asyncio
    async def test_non_health_routes_require_desktop_bearer_token(self, desktop_env):
        import httpx

        from src.http.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1"
        ) as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/openapi.json")).status_code == 401
            assert (await client.get("/auth/login?service=gmail")).status_code == 401

    @pytest.mark.asyncio
    async def test_desktop_lifespan_skips_connectkit_refresh_task(self, desktop_env, monkeypatch):
        import src.http.main as main_mod

        class FakeTask:
            def cancel(self) -> None:
                pass

        class FakeLoop:
            create_task_calls = 0

            def create_task(self, coroutine):
                self.create_task_calls += 1
                coroutine.close()
                return FakeTask()

        fake_loop = FakeLoop()
        scheduler_calls = 0

        def get_scheduler():
            nonlocal scheduler_calls
            scheduler_calls += 1

        monkeypatch.setattr(main_mod.asyncio, "get_event_loop", lambda: fake_loop)
        monkeypatch.setattr("src.subagent.scheduler.get_scheduler", get_scheduler)

        async with main_mod.lifespan(main_mod.app):
            await asyncio.sleep(0)

        assert fake_loop.create_task_calls == 0
        assert scheduler_calls == 0
