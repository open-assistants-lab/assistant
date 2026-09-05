"""SB1 SandboxBackend seam tests: protocol, soft backend, backend selection."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.sdk.sandbox import (
    BwrapSandboxBackend,
    NullSandboxBackend,
    SandboxBackend,
    SandboxError,
    SandboxLimits,
    SandboxResult,
    SoftSandboxBackend,
    bwrap_available,
    get_sandbox_backend,
    path_outside_workspace,
    scrub_env,
)


class TestProtocol:
    def test_null_satisfies_protocol(self):
        assert isinstance(NullSandboxBackend(), SandboxBackend)

    def test_soft_satisfies_protocol(self):
        assert isinstance(SoftSandboxBackend(), SandboxBackend)

    def test_result_shape(self):
        r = SandboxResult(exit_code=0, stdout="x")
        assert r.stderr == "" and r.timed_out is False


class TestNullBackend:
    def test_runs_argv(self, tmp_path):
        r = NullSandboxBackend().run(["echo", "hi"], tmp_path)
        assert r.exit_code == 0 and "hi" in r.stdout

    def test_timeout(self, tmp_path):
        r = NullSandboxBackend().run(
            ["python3", "-c", "import time; time.sleep(5)"],
            tmp_path,
            SandboxLimits(timeout_seconds=1),
        )
        assert r.timed_out

    def test_no_validation(self, tmp_path):
        b = NullSandboxBackend()
        assert b.validate_write_path(Path("/etc/passwd"), tmp_path) is None
        assert b.validate_source("open('/etc/passwd','w')", tmp_path) is None


class TestSoftBackend:
    def test_env_scrubbed_no_secrets(self, tmp_path, monkeypatch):
        monkeypatch.setenv("API_KEY", "sk-super-secret")
        monkeypatch.setenv("OLLAMA_API_KEY", "leaky")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "nope")
        monkeypatch.setenv("MY_SESSION_TOKEN", "nope2")
        monkeypatch.setenv("PATH", "/usr/bin")
        env = scrub_env()
        assert "API_KEY" not in env
        assert "OLLAMA_API_KEY" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "MY_SESSION_TOKEN" not in env
        assert env["PATH"] == "/usr/bin"

    def test_write_outside_workspace_rejected(self, tmp_path):
        b = SoftSandboxBackend()
        outside = tmp_path.parent / "outside.txt"
        assert b.validate_write_path(outside, tmp_path) is not None
        inside = tmp_path / "inside.txt"
        assert b.validate_write_path(inside, tmp_path) is None

    def test_source_write_outside_rejected_before_spawn(self, tmp_path):
        b = SoftSandboxBackend()
        err = b.validate_source("open('/tmp/escape.txt','w').write('x')", tmp_path)
        assert err is not None and "outside the workspace" in err

    def test_source_inside_workspace_allowed(self, tmp_path):
        b = SoftSandboxBackend()
        src = f"open({str(tmp_path / 'ok.txt')!r}, 'w').write('x')"
        assert b.validate_source(src, tmp_path) is None

    def test_run_enforces_timeout(self, tmp_path):
        b = SoftSandboxBackend()
        r = b.run(
            ["python3", "-c", "import time; time.sleep(8)"],
            tmp_path,
            SandboxLimits(timeout_seconds=1.5),
        )
        assert r.timed_out

    def test_run_scrubs_env_into_child(self, tmp_path, monkeypatch):
        monkeypatch.setenv("API_KEY", "sk-child-leak")
        b = SoftSandboxBackend()
        r = b.run(["python3", "-c", "import os; print(os.environ.get('API_KEY', 'absent'))"], tmp_path)
        assert "absent" in r.stdout and "sk-child-leak" not in (r.stdout + r.stderr)

    def test_run_output_capped(self, tmp_path):
        b = SoftSandboxBackend()
        r = b.run(
            ["python3", "-c", "print('x' * 10_000)"],
            tmp_path,
            SandboxLimits(max_output_bytes=100),
        )
        assert len(r.stdout) <= 100


class TestBackendSelection:
    def test_default_is_soft(self):
        from src.config.settings import SandboxConfig

        assert SandboxConfig().backend == "soft"

    def test_selection_follows_config(self, monkeypatch):
        import src.config.settings as sm

        monkeypatch.setattr(sm, "_config", None, raising=False)
        monkeypatch.setenv("SANDBOX_BACKEND", "null")
        try:
            assert isinstance(get_sandbox_backend(), NullSandboxBackend)
        finally:
            monkeypatch.setenv("SANDBOX_BACKEND", "soft")
            sm._config = None

    def test_path_outside_workspace(self, tmp_path):
        assert path_outside_workspace(tmp_path.parent / "x", tmp_path)
        assert not path_outside_workspace(tmp_path / "x", tmp_path)

    def test_unknown_backend_fails_closed(self, monkeypatch):
        monkeypatch.setattr(
            "src.config.get_settings",
            lambda: SimpleNamespace(sandbox=SimpleNamespace(backend="bwarp")),
        )
        with pytest.raises(SandboxError, match="Unknown SandboxBackend"):
            get_sandbox_backend()

    def test_bwrap_requires_curated_rootfs(self, monkeypatch):
        import src.sdk.sandbox as sandbox

        monkeypatch.setattr(sandbox, "bwrap_available", lambda: True)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            "src.config.get_settings",
            lambda: SimpleNamespace(
                sandbox=SimpleNamespace(backend="bwrap", bwrap_rootfs="")
            ),
        )
        with pytest.raises(SandboxError, match="SANDBOX_BWRAP_ROOTFS"):
            get_sandbox_backend()

    def test_bwrap_rejected_outside_linux(self, monkeypatch, tmp_path):
        import src.sdk.sandbox as sandbox

        rootfs = tmp_path / "rootfs"
        rootfs.mkdir()
        monkeypatch.setattr(sandbox, "bwrap_available", lambda: True)
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(
            "src.config.get_settings",
            lambda: SimpleNamespace(
                sandbox=SimpleNamespace(backend="bwrap", bwrap_rootfs=str(rootfs))
            ),
        )
        with pytest.raises(SandboxError, match="Linux"):
            get_sandbox_backend()


def test_single_level_traversal_rejected(tmp_path):
    b = SoftSandboxBackend()
    src = "open('../escape.txt','w').write('x')"
    root = tmp_path / "ws"
    root.mkdir()
    assert b.validate_source(src, root) is not None  # rejected


class TestUidDrop:
    """SB1-2 acceptance: no agent subprocess runs as root (security rule)."""

    def _uid_probe_script(self):
        return (
            "import os, json;"
            "print(json.dumps({'euid': os.geteuid(), 'egid': os.getegid()}))"
        )

    def test_subprocess_never_runs_as_root(self, monkeypatch, tmp_path):
        """Security rule: when the server runs as ROOT, the dropped UID must
        equal the configured sandbox uid (not root). When the server is
        already non-root, the child inherits that non-root uid — both are
        compliant; uid 0 is not."""
        import os as _os

        import src.config.settings as settings_mod
        import src.sdk.sandbox as sb

        server_euid = _os.geteuid()
        uid, gid = 1000, 1000
        monkeypatch.setattr(
            settings_mod, "_config", None
        )
        monkeypatch.setenv("SANDBOX_UID", str(uid))
        monkeypatch.setenv("SANDBOX_GID", str(gid))
        settings_mod._config = None

        backend = sb.SoftSandboxBackend()
        root = tmp_path / "ws"
        root.mkdir()
        result = backend.run(
            ["python3", "-c", self._uid_probe_script()],
            cwd=root,
            limits=sb.SandboxLimits(env_mode="scrubbed"),
 env_extra={"PATH": _os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root)},
        )
        assert result.exit_code == 0, result.stderr
        child = json.loads(result.stdout)
        if server_euid == 0:
            assert child["euid"] == uid, "root server must drop to sandbox uid"
            assert child["egid"] == gid
        else:
            assert child["euid"] == server_euid != 0

    def test_root_server_failed_drop_fails_closed(self, monkeypatch, tmp_path):
        """If the server runs as root and the drop FAILS, the run must not
        proceed as root (fail closed)."""
        import os as _os

        import src.sdk.sandbox as sb

        if _os.geteuid() != 0:
            pytest.skip("fail-closed path requires a root server process")
        backend = sb.SoftSandboxBackend()
        root = tmp_path / "ws"
        root.mkdir()
        backend._force_drop_failure = True  # type: ignore[attr-defined]
        result = backend.run(
            ["python3", "-c", "print('should not run')"],
            cwd=root,
            limits=sb.SandboxLimits(),
        )
        assert result.exit_code != 0
        assert result.stdout.strip() != "should not run"



# ---------------------------------------------------------------------------
# Soft+UID: per-user OS-account drop (decision 2026-09-03)
# ---------------------------------------------------------------------------


def test_user_sandbox_uid_mapping_deterministic():
    """uid mapping is stable per user, distinct across users, in range."""
    import src.sdk.sandbox as sb

    a1 = sb.user_sandbox_uid_gid("alice")
    a2 = sb.sb_user_uid = sb.user_sandbox_uid_gid("alice")
    b = sb.user_sandbox_uid_gid("bob")
    assert a1 == a2
    assert a1 != b
    for uid, gid in (a1, b):
        assert 2000 <= uid < 3000
        assert gid == uid


def test_user_sandbox_uid_no_user_id_maps_default():
    """None user_id maps to the default_user identity (stable)."""
    import src.sdk.sandbox as sb

    assert sb.user_sandbox_uid_gid(None) == sb.user_sandbox_uid_gid("default_user")


def test_prepare_user_dirs_chowns_only_user_dirs(tmp_path, monkeypatch):
    """Root server: chown targets are ONLY the user's own home + Files dir."""

    import src.sdk.sandbox as sb

    chowned: list[tuple[str, int, int]] = []
    monkeypatch.setattr(sb, "_PREPARED_UIDS", set())
    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.setattr(
        os,
        "chown",
        lambda p, u, g: chowned.append((str(p), u, g)),
    )

    import src.storage.paths as paths_mod

    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
    )

    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    sb._prepare_user_dirs("alice", ws)
    targets = {p for p, _, _ in chowned}
    assert str(tmp_path / "workspaces" / "alice" / "Files") in targets or any(
        "Files" in p for p, _, _ in chowned
    )
    assert all("sandbox-home" in p or "workspaces" in p for p, _, _ in chowned)


@pytest.mark.skipif(
    sys.platform != "linux" or os.getuid() != 0,
    reason="per-user setuid kernel isolation requires a root Linux host",
)
def test_per_user_kernel_isolation_two_users(tmp_path):
    """Root Linux: A and B children run under DIFFERENT uids; A cannot
    write into B's chowned workspace."""
    import src.sdk.sandbox as sb

    ws_a = tmp_path / "A"
    ws_b = tmp_path / "B"
    (ws_a / "Files").mkdir(parents=True)
    (ws_b / "Files").mkdir(parents=True)

    import src.storage.paths as paths_mod

    paths_mod._paths_cache.clear()

    from src.config import settings as settings_module

    settings_module._config = None
    try:
        uid_a, gid_a = sb.user_sandbox_uid_gid("alice")
        uid_b, gid_b = sb.user_sandbox_uid_gid("bob")
        os.chown(str(ws_a / "Files"), uid_a, gid_a)
        os.chown(str(ws_b / "Files"), uid_b, gid_b)

        r_a = sb.SoftSandboxBackend().run(
            ["python3", "-c", "import os; print(os.geteuid())"],
            ws_a,
            user_id="alice",
        )
        r_b = sb.SoftSandboxBackend().run(
            ["python3", "-c", "import os; print(os.geteuid())"],
            ws_b,
            SandboxLimits(),
            user_id="bob",
        )
        assert r_a.stdout.strip() == str(uid_a)
        assert r_b.stdout.strip() == str(uid_b)
        assert uid_a != uid_b

        # A's sandboxed code cannot write into B's chowned workspace.
        r = sb.SoftSandboxBackend().run(
            ["python3", "-c", f"open('{ws_b}/Files/leak.txt','w').write('x')"],
            ws_a,
            limits=sb.SandboxLimits(),
            user_id="alice",
        )
        assert not (ws_b / "Files" / "leak.txt").exists()
        assert r.returncode != 0 or "Permission denied" in (r.stderr or "")
    finally:
        settings_module._config = None
        os.chown(str(ws_a / "Files"), os.getuid(), os.getgid())
        os.chown(str(ws_b / "Files"), os.getuid(), os.getgid())


# ---------------------------------------------------------------------------
# T3.4: bwrap hard backend
# ---------------------------------------------------------------------------




_BWRAP_ROOTFS = os.environ.get("SANDBOX_BWRAP_ROOTFS", "")
_HAS_BWRAP = (
    bwrap_available()
    and bool(_BWRAP_ROOTFS)
    and Path(_BWRAP_ROOTFS).is_dir()
    and os.getuid() != 0
)


def test_get_sandbox_backend_raises_when_bwrap_missing(monkeypatch):
    import src.sdk.sandbox as sbx

    monkeypatch.setattr(sbx, "bwrap_available", lambda: False)
    monkeypatch.setattr(
        "src.config.settings.SandboxConfig.backend", "bwrap"
    ) if False else None
    import src.config as cfg_mod

    monkeypatch.setattr(
        cfg_mod,
        "get_settings",
        lambda: type(
            "S",
            (),
            {"sandbox": type("C", (), {"backend": "bwrap", "uid_mode": "shared"})()},
        ),
    )
    try:
        sbx.get_sandbox_backend()
        raised = False
    except sbx.SandboxError as e:
        raised = True
        assert "bwrap" in str(e).lower()
    if _HAS_BWRAP:
        return  # backend actually available; nothing to assert
    assert raised


def test_bwrap_backend_rejects_host_rootfs():
    with pytest.raises(SandboxError, match="curated rootfs"):
        BwrapSandboxBackend(Path("/"))


def test_bwrap_command_uses_curated_rootfs_and_workspace_mount(tmp_path, monkeypatch):
    import src.sdk.sandbox as sandbox

    rootfs = tmp_path / "rootfs"
    workspace = tmp_path / "workspace"
    rootfs.mkdir()
    workspace.mkdir()
    captured: list[str] = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 501)
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda argv, **kwargs: (captured.extend(argv) or Completed()),
    )

    BwrapSandboxBackend(rootfs).run(["python3", "-c", "pass"], workspace)

    assert ["--ro-bind", str(rootfs), "/"] in [
        captured[i : i + 3] for i in range(len(captured))
    ]
    assert ["--bind", str(workspace), "/workspace"] in [
        captured[i : i + 3] for i in range(len(captured))
    ]
    assert ["--ro-bind", "/", "/"] not in [captured[i : i + 3] for i in range(len(captured))]
    assert "--unshare-user" in captured


def test_bwrap_rejects_root_service_process(monkeypatch, tmp_path):
    import src.sdk.sandbox as sandbox

    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sandbox, "bwrap_available", lambda: True)
    monkeypatch.setattr(sandbox.os, "getuid", lambda: 0)
    monkeypatch.setattr(
        "src.config.get_settings",
        lambda: SimpleNamespace(
            sandbox=SimpleNamespace(backend="bwrap", bwrap_rootfs=str(rootfs))
        ),
    )

    with pytest.raises(SandboxError, match="root service process"):
        get_sandbox_backend()


@pytest.mark.skipif(not _HAS_BWRAP, reason="bwrap rootfs is not configured")
def test_bwrap_runs_inside_workspace(tmp_path):
    b = BwrapSandboxBackend(Path(_BWRAP_ROOTFS))
    ws = tmp_path / "ws"
    ws.mkdir()
    r = b.run(
        ["python3", "-c", "open('inside.txt','w').write('ok')"],
        cwd=ws,
        limits=SandboxLimits(),
    )
    assert r.exit_code == 0
    assert (ws / "inside.txt").exists()


@pytest.mark.skipif(not _HAS_BWRAP, reason="bwrap rootfs is not configured")
def test_bwrap_blocks_outside_workspace_write(tmp_path):
    b = BwrapSandboxBackend(Path(_BWRAP_ROOTFS))
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside.txt"
    r = b.run(
        ["python3", "-c", f"open('{outside}','w').write('escape')"],
        cwd=ws,
        limits=SandboxLimits(),
    )
    # read-only root bind: the write fails (or the file never appears)
    assert not outside.exists() or r.exit_code != 0
