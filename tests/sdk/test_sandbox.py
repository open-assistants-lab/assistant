"""SB1 SandboxBackend seam tests: protocol, soft backend, backend selection."""

from pathlib import Path

import json

import pytest

from src.sdk.sandbox import (
    NullSandboxBackend,
    SandboxBackend,
    SandboxLimits,
    SandboxResult,
    SoftSandboxBackend,
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

from src.sdk.sandbox import SoftSandboxBackend, SandboxLimits

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
