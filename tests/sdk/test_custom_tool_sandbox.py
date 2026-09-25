"""Issue #34: custom TOOL.md commands must run through the sandbox seam.

Both wrappers — `_parse_tool_file` (freshly parsed) and
`_rebuild_custom_function` (rebuilt from the index) — used to call
`subprocess.run(rendered, shell=True)` directly. That path skipped every cap
the seam applies to `shell_execute`: the write budget (RLIMIT_FSIZE), the
CPU/address-space limits, the uid drop, and the scrubbed environment.

These tests pin the transport (one `sh -c` invocation through the backend),
the operator-tunable limits, and the write budget — for both wrappers.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.sdk.tool_index import _rebuild_custom_function
from src.sdk.tool_results import CommandKilledError
from src.sdk.tools import ToolDefinition
from src.sdk.tools_custom import _parse_tool_file


@pytest.fixture(autouse=True)
def soft_backend(monkeypatch):
    """Pin a limiting backend: `null` is a documented no-caps passthrough."""
    from src.config import reload_settings

    monkeypatch.setenv("SANDBOX_BACKEND", "soft")
    reload_settings()
    yield
    reload_settings()


@pytest.fixture
def scoped_paths(tmp_path, monkeypatch):
    """Bind paths.get_paths to a temp data_root for this test."""
    from src.storage.paths import DataPaths

    def get_paths(user_id="default_user", workspace_id="personal"):
        return DataPaths(
            user_id=user_id,
            workspace_id=workspace_id,
            data_root=tmp_path / "data",
            data_path=tmp_path / "settings",
        )

    monkeypatch.setattr("src.storage.paths.get_paths", get_paths)
    return get_paths


def test_custom_command_receives_explicit_sandbox_secret_allowlist(
    tmp_path, scoped_paths, monkeypatch
):
    from src.sdk.tools_custom import run_custom_command

    monkeypatch.setenv("JEN_BRIDGE_KEY", "bridge-secret")
    monkeypatch.setattr(
        "src.config.get_settings",
        lambda: SimpleNamespace(
            shell_tool=SimpleNamespace(max_output_kb=100, max_write_mb=64),
            sandbox=SimpleNamespace(env_allow=["JEN_BRIDGE_KEY"]),
        ),
    )

    result = run_custom_command('printf %s "$JEN_BRIDGE_KEY"', "alice")

    assert result == "bridge-secret"


def make_tool(tmp_path, mode, command, annotations=None):
    annotations = annotations or {}
    if mode == "reconstructed":
        td = ToolDefinition(name="fixture_command", description="Sandbox fixture")
        for field, value in annotations.items():
            setattr(td.annotations, field, value)
        return _rebuild_custom_function(td, {"command": command, "install": []})
    lines = [
        "---",
        "name: fixture_command",
        "description: Sandbox fixture",
        f"command: {command}",
    ]
    if annotations:
        lines.append("annotations:")
        lines += [f"  {field}: {value}" for field, value in annotations.items()]
    lines.append("---")
    tool_file = tmp_path / "TOOL.md"
    tool_file.write_text("\n".join(lines) + "\n")
    td = _parse_tool_file(tool_file)
    assert td is not None, tool_file.read_text()
    return td


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
def test_custom_command_goes_through_the_seam_as_one_shell_argv(
    tmp_path, mode, scoped_paths, monkeypatch
):
    """The seam receives `["sh", "-c", rendered]` plus the workspace cwd."""
    from src.sdk.sandbox import SandboxResult

    monkeypatch.setattr(
        "src.config.get_settings",
        lambda: SimpleNamespace(
            sandbox=None,
            shell_tool=SimpleNamespace(max_output_kb=100, max_write_mb=3),
        ),
    )
    seen: dict = {}

    class RecordingBackend:
        def run(self, argv, cwd, limits=None, *, env_extra=None, user_id=None):
            seen.update(argv=argv, cwd=cwd, limits=limits, user_id=user_id)
            return SandboxResult(0, "ok", "")

    monkeypatch.setattr(
        "src.sdk.sandbox.get_sandbox_backend", lambda: RecordingBackend()
    )

    td = make_tool(tmp_path, mode, 'echo "{{message}}"')
    assert td.function(message="hello") == "ok"

    assert seen["argv"] == ["sh", "-c", 'echo "hello"']
    assert seen["cwd"] == scoped_paths().workspace_files_dir()
    assert seen["user_id"] == "default_user"
    assert seen["limits"].timeout_seconds == 300.0
    assert seen["limits"].max_output_bytes == 100 * 1024
    assert seen["limits"].max_write_bytes == 3 * 1024 * 1024


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
def test_custom_command_cannot_exceed_the_write_budget(
    tmp_path, mode, scoped_paths, monkeypatch
):
    """A custom command that exceeds the write budget must fail, not fill disk.

    Exceeding RLIMIT_FSIZE surfaces as SIGXFSZ on some platforms and as EFBIG
    (a plain non-zero exit) on others — the honest assertion is that the
    over-budget write did not succeed, and that the tool reported failure
    rather than returning a success string.
    """
    from src.config import reload_settings

    monkeypatch.setenv("SHELL_TOOL_MAX_WRITE_MB", "1")
    reload_settings()

    requested = 2 * 1024 * 1024
    td = make_tool(
        tmp_path,
        mode,
        f"python3 -c \"open('big.bin','wb').write(b'x'*{requested})\"",
    )
    target = scoped_paths().workspace_files_dir() / "big.bin"

    try:
        result = td.function()
    except CommandKilledError:
        pass  # the resource cap killed the writer — the strong outcome
    else:
        assert isinstance(result, str) and "Command failed" in result, result

    assert not target.exists() or target.stat().st_size < requested, (
        "the write budget did not bound the custom command"
    )


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
def test_unbounded_timeout_declaration_still_executes(tmp_path, mode, scoped_paths):
    """`timeout_seconds: none` (#23) must survive the seam.

    The soft backend's preexec caps RLIMIT_CPU, which cannot be derived from
    an unbounded declaration — it must skip that cap instead of failing the
    child.
    """
    td = make_tool(tmp_path, mode, "echo fixture", {"timeout_seconds": "none"})

    result = td.function()
    assert "fixture" in str(result)
