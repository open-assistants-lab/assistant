"""Issue #32 part 3: the write budget is its own number.

`RLIMIT_FSIZE` was derived from `max_output_bytes * 8` — roughly 800 KB with
the shipped defaults — so a command that legitimately wrote a larger file
(downloading an asset, generating a report) was killed. It was also coupled to
output capture, so raising the stdout budget silently raised the write cap.

The two are now independent: `SandboxLimits.max_write_bytes` (default 64 MB,
configurable as `shell_tool.max_write_mb`) bounds writes, while output capture
keeps its own headroom.
"""

from __future__ import annotations

import os

import pytest

from src.sdk.sandbox import SandboxLimits, get_sandbox_backend


@pytest.fixture(autouse=True)
def _limiting_backend(monkeypatch):
    """Pin the backend: with `null` these tests pass vacuously.

    `null` is a documented passthrough that applies no rlimits, so an ambient
    SANDBOX_BACKEND=null would silently stop the anchor tests proving anything.
    """
    from src.config import reload_settings

    monkeypatch.setenv("SANDBOX_BACKEND", "soft")
    reload_settings()
    yield
    reload_settings()


def _run(argv: list[str], cwd, **limit_kwargs):
    return get_sandbox_backend().run(
        argv, cwd, SandboxLimits(timeout_seconds=30.0, **limit_kwargs)
    )


def _write_file_script(path: str, size: int) -> list[str]:
    return [
        "python3",
        "-c",
        f"open({path!r}, 'wb').write(b'x' * {size})",
    ]


def test_a_write_above_the_old_output_derived_cap_succeeds(tmp_path):
    """A write past the old derived cap must no longer be killed.

    The old limit was max_output_bytes * 8 — 1.6 MB for this class's default
    (200 KB) and 800 KB with the shipped shell_tool config (100 KB). 4 MB
    exceeds both, so this fails against the derived cap rather than passing
    inside it.
    """
    target = tmp_path / "report.bin"
    size = 4 * 1024 * 1024

    result = _run(_write_file_script(str(target), size), tmp_path)

    assert result.exit_code == 0, (result.exit_code, result.stderr)
    assert result.signalled is False, result
    assert target.stat().st_size == size


def test_write_budget_is_independent_of_the_output_budget(tmp_path):
    """A tiny output budget must not shrink the write budget."""
    target = tmp_path / "big.bin"
    size = 512 * 1024  # 512 KB: above max_output_bytes * 8 = 8 KB here

    result = _run(
        _write_file_script(str(target), size),
        tmp_path,
        max_output_bytes=1024,        # 1 KB of captured output
        max_write_bytes=64 * 1024 * 1024,
    )

    assert result.exit_code == 0, (result.exit_code, result.stderr)
    assert target.stat().st_size == size


def test_the_write_budget_is_enforced_when_exceeded(tmp_path):
    """The cap still bites, however the platform reports it.

    Exceeding RLIMIT_FSIZE surfaces as SIGXFSZ on some platforms and as
    EFBIG (a plain non-zero exit) on others — so the honest assertion is that
    the over-budget write did not succeed, not which mechanism stopped it.
    """
    target = tmp_path / "too_big.bin"
    requested = 2 * 1024 * 1024

    result = _run(
        _write_file_script(str(target), requested),
        tmp_path,
        max_write_bytes=256 * 1024,
    )

    assert result.signalled is True or result.exit_code != 0, result
    assert not target.exists() or target.stat().st_size < requested, (
        "the write budget did not bound the write"
    )


def test_default_write_budget_is_64mb():
    """The shipped default is the number that was agreed, not a derived one."""
    assert SandboxLimits().max_write_bytes == 64 * 1024 * 1024


def test_configured_budget_reaches_the_limit(monkeypatch, tmp_path):
    """shell_tool.max_write_mb is what the tools actually pass through."""
    from src.sdk.tools_core import shell as shell_mod

    monkeypatch.setattr(
        shell_mod,
        "_get_shell_config",
        lambda: {
            "allowed_commands": {"python3"},
            "timeout_seconds": 30,
            "max_output_kb": 100,
            "max_write_mb": 3,
        },
    )
    monkeypatch.setattr(shell_mod, "_get_root_path", lambda *a, **k: tmp_path)

    seen: dict[str, int] = {}

    class _Result:
        exit_code = 0
        stdout = ""
        stderr = ""
        timed_out = False
        signalled = False
        stdout_truncated = False

    class _Backend:
        def run(self, argv, cwd, limits=None, **kwargs):
            seen["max_write_bytes"] = limits.max_write_bytes
            return _Result()

        def validate_source(self, *a, **k):
            return None

        def validate_write_path(self, *a, **k):
            return None

    backend = _Backend()
    monkeypatch.setattr("src.sdk.sandbox.get_sandbox_backend", lambda: backend)
    monkeypatch.setattr(shell_mod, "get_sandbox_backend", lambda: backend, raising=False)

    shell_mod.shell_execute.function(command="python3 -c 'pass'", user_id="u")

    assert seen["max_write_bytes"] == 3 * 1024 * 1024


def test_env_override_is_read(monkeypatch):
    """Operators can tune it without a code change."""
    monkeypatch.setenv("SHELL_TOOL_MAX_WRITE_MB", "16")
    from src.config import reload_settings
    from src.config.settings import get_settings

    reload_settings()
    try:
        assert get_settings().shell_tool.max_write_mb == 16
    finally:
        monkeypatch.delenv("SHELL_TOOL_MAX_WRITE_MB")
        reload_settings()


def test_soft_backend_applies_the_configured_budget(monkeypatch, tmp_path):
    """The cap is set wherever RLIMIT_FSIZE is set.

    Only the limiting backends are covered: `null` is a dev/test passthrough
    that applies no rlimits by design, and `bwrap` is Linux-only.
    """
    if os.name != "posix":  # pragma: no cover - the seam is POSIX-only
        pytest.skip("RLIMIT_FSIZE is POSIX-only")
    target = tmp_path / "small.bin"
    result = _run(
        _write_file_script(str(target), 4 * 1024 * 1024),
        tmp_path,
        max_write_bytes=1024 * 1024,  # 1 MB budget
    )

    assert result.signalled is True or result.exit_code != 0, result


def test_host_hard_limit_below_the_default_does_not_fail_the_command(
    tmp_path, monkeypatch
):
    """A host whose hard FSIZE is under 64 MB must still run commands.

    An unprivileged process cannot raise a hard limit, so asking for the full
    default on such a host would raise inside preexec_fn and fail every
    sandboxed command. The limit is clamped to the host's hard limit instead —
    the host's policy stays in force rather than the cap silently vanishing.
    """
    import resource

    from src.sdk import sandbox as sandbox_mod

    applied: list[tuple[int, int]] = []
    real_setrlimit = resource.setrlimit
    original_hard = resource.getrlimit(resource.RLIMIT_FSIZE)[1]

    def fake_getrlimit(which):
        if which == resource.RLIMIT_FSIZE:
            # A host that allows only 2 MB per file — between the old derived
            # cap and the new default.
            return (2 * 1024 * 1024, 2 * 1024 * 1024)
        return resource.getrlimit(which)

    def recording_setrlimit(which, limits):
        if which == resource.RLIMIT_FSIZE:
            applied.append(limits)
            return
        return real_setrlimit(which, limits)

    monkeypatch.setattr(resource, "getrlimit", fake_getrlimit)
    monkeypatch.setattr(resource, "setrlimit", recording_setrlimit)
    try:
        sandbox_mod._apply_write_limit(resource, 64 * 1024 * 1024)
    finally:
        monkeypatch.undo()

    assert applied == [(2 * 1024 * 1024, 2 * 1024 * 1024)], applied
    assert resource.getrlimit(resource.RLIMIT_FSIZE)[1] == original_hard


def test_clamp_is_skipped_when_the_host_is_unlimited(monkeypatch):
    """An unlimited host gets the configured budget untouched."""
    import resource

    from src.sdk import sandbox as sandbox_mod

    applied: list[tuple[int, int]] = []
    monkeypatch.setattr(
        resource,
        "getrlimit",
        lambda which: (resource.RLIM_INFINITY, resource.RLIM_INFINITY),
    )
    monkeypatch.setattr(
        resource,
        "setrlimit",
        lambda which, limits: applied.append(limits),
    )

    sandbox_mod._apply_write_limit(resource, 7 * 1024 * 1024)

    assert applied == [(7 * 1024 * 1024, resource.RLIM_INFINITY)], applied


def test_code_execute_reads_the_configured_write_budget(monkeypatch):
    """The code path is wired identically to shell_execute."""
    from types import SimpleNamespace

    from src.sdk.tools_core import code_execute as ce

    monkeypatch.setattr(
        ce,
        "get_settings",
        lambda: SimpleNamespace(
            shell_tool=SimpleNamespace(
                timeout_seconds=30, max_output_kb=100, max_write_mb=5
            )
        ),
    )

    assert ce._get_limits().max_write_bytes == 5 * 1024 * 1024
