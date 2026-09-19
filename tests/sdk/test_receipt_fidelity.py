"""Receipt fidelity: a governed run must report what actually happened.

#26 — a tool returning `ToolResult` lost its `is_error` flag (hard-coded False
on the success-return path) and its content became a pydantic repr via
`json.dumps(..., default=str)`, so a tool that ran and failed was receipted as
completed.

#25 — a child killed by a signal (RLIMIT_AS/CPU/NPROC/FSIZE) returns a negative
exit code with `timed_out=False`, which is indistinguishable from an ordinary
non-zero exit, so the run was receipted `executed: true`. Timeouts also use
`exit_code=-1`, so signals must be marked explicitly rather than inferred.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from unittest.mock import patch

import pytest

import src.storage.paths as paths_mod
from src.sdk.sandbox import SandboxLimits, SandboxResult, get_sandbox_backend
from src.sdk.tool_results import CommandKilledError, raise_command_killed
from src.sdk.tools import ToolDefinition, ToolResult

USER = "dave"


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths_mod.DataPaths, "root", property(lambda self: root))
    import src.sdk.capabilities as caps_mod

    monkeypatch.setattr(caps_mod, "user_capabilities_root", lambda user_id: root / user_id)
    import src.sdk.governance as gov

    monkeypatch.setattr(gov, "_services", {})
    return root


def _service(monkeypatch):
    from src.sdk.governance import GovernanceService

    svc = GovernanceService()
    monkeypatch.setattr(svc, "resolve_tier", lambda *_: "explicit")
    monkeypatch.setattr(svc, "_log_execution_result", lambda *_: None)
    monkeypatch.setattr(svc, "_emit_receipt", lambda *a, **k: None)
    return svc


def _run_governed(monkeypatch, td: ToolDefinition, arguments: dict | None = None):
    svc = _service(monkeypatch)
    proposal_id = svc.create_pending(USER, td.name, arguments or {}, tier="explicit")
    svc.approve(USER, proposal_id)
    return asyncio.run(svc.execute_approved(USER, proposal_id, registry=[td]))


# --- #26: ToolResult propagation -------------------------------------------


def test_governed_tool_result_error_is_receipted_as_failed(monkeypatch):
    """A tool that ran and failed must not be recorded as completed."""
    td = ToolDefinition(
        name="probe",
        description="d",
        function=lambda **_: ToolResult(
            content="real failure message",
            structured_content={"reason": "upstream"},
            is_error=True,
        ),
    )

    result = _run_governed(monkeypatch, td)

    assert result["is_error"] is True, result
    assert result["content"] == "real failure message", result
    assert result["structured_content"]["reason"] == "upstream", result
    assert result["structured_content"]["executed"] is True, result
    assert result["structured_content"]["tool"] == "probe", result


def test_governance_keys_win_over_a_tools_structured_content(monkeypatch):
    """The receipt is authoritative: a tool cannot spoof executed/tool."""
    td = ToolDefinition(
        name="probe",
        description="d",
        function=lambda **_: ToolResult(
            content="ran",
            structured_content={"executed": False, "tool": "spoofed"},
        ),
    )

    result = _run_governed(monkeypatch, td)

    assert result["structured_content"]["executed"] is True, result
    assert result["structured_content"]["tool"] == "probe", result


def test_governed_tool_result_content_is_not_stringified(monkeypatch):
    """Content must be the tool's text, never a pydantic repr."""
    td = ToolDefinition(
        name="probe",
        description="d",
        function=lambda **_: ToolResult(content='search "results" ok'),
    )

    result = _run_governed(monkeypatch, td)

    assert result["content"] == 'search "results" ok', result
    assert "content=" not in result["content"], "content was stringified from the model"
    assert result["is_error"] is False, result
    assert result["structured_content"]["executed"] is True, result


def test_governed_plain_returns_keep_their_shape(monkeypatch):
    """Strings and non-ToolResult values behave as before."""
    as_str = _run_governed(
        monkeypatch,
        ToolDefinition(name="probe", description="d", function=lambda **_: "plain"),
    )
    assert as_str["content"] == "plain" and as_str["is_error"] is False, as_str
    assert as_str["structured_content"]["executed"] is True, as_str


# --- #25: signal kills -----------------------------------------------------


def test_sandbox_marks_a_signal_kill(tmp_path):
    """A SIGKILL'd child reports a negative code and signalled=True."""
    result = get_sandbox_backend().run(
        [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"],
        tmp_path,
        SandboxLimits(timeout_seconds=30.0),
    )
    assert result.exit_code < 0, result
    assert result.signalled is True, result
    assert result.timed_out is False, result


def test_sandbox_ordinary_non_zero_exit_is_not_signalled(tmp_path):
    """`grep`-style non-zero exits must keep returning their output."""
    result = get_sandbox_backend().run(
        [sys.executable, "-c", "import sys; print('no match'); sys.exit(1)"],
        tmp_path,
        SandboxLimits(timeout_seconds=30.0),
    )
    assert result.exit_code == 1, result
    assert result.signalled is False, result
    assert "no match" in result.stdout, result


def test_sandbox_timeout_is_not_reported_as_a_signal_kill(tmp_path):
    """Timeouts also use exit_code=-1, so signalled must stay False."""
    from src.sdk import sandbox as sandbox_mod

    backend = sandbox_mod.SoftSandboxBackend()
    with patch(
        "subprocess.Popen.communicate",
        side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1),
    ):
        result = backend.run([sys.executable, "-c", "pass"], tmp_path, SandboxLimits(timeout_seconds=1.0))
    assert result.timed_out is True, result
    assert result.signalled is False, result


@pytest.mark.parametrize("tool_name", ["shell_execute", "code_execute"])
def test_native_tool_raises_on_a_signal_kill(tool_name, tmp_path, monkeypatch):
    """A killed command must fail rather than return its partial output."""
    import src.sdk.tools_core.code_execute as ce
    import src.sdk.tools_core.shell as shell

    killed = SandboxResult(exit_code=-9, stdout="partial", stderr="", signalled=True)
    backend = type(
        "B",
        (),
        {
            "run": lambda *a, **k: killed,
            "validate_source": lambda *a, **k: None,
            "validate_write_path": lambda *a, **k: None,
        },
    )()

    if tool_name == "shell_execute":
        monkeypatch.setattr(shell, "_get_root_path", lambda *a, **k: tmp_path)
        monkeypatch.setattr("src.sdk.sandbox.get_sandbox_backend", lambda: backend)
        td, args = shell.shell_execute, {"command": "echo hi", "user_id": USER}
    else:
        monkeypatch.setattr(ce, "_workspace_root", lambda *a, **k: tmp_path)
        monkeypatch.setattr(ce, "_get_limits", lambda: SandboxLimits(timeout_seconds=30.0))
        monkeypatch.setattr(ce, "get_sandbox_backend", lambda: backend, raising=False)
        monkeypatch.setattr("src.sdk.sandbox.get_sandbox_backend", lambda: backend)
        td, args = ce.code_execute, {"code": "print(1)", "user_id": USER}

    with pytest.raises(CommandKilledError) as exc:
        td.function(**args)
    assert "killed" in str(exc.value).lower()
    assert exc.value.signal_number == 9, exc.value


def test_governed_signal_kill_is_not_receipted_as_executed(monkeypatch):
    """End-to-end: a killed run must not claim it completed."""
    td = ToolDefinition(
        name="probe",
        description="d",
        function=lambda **_: raise_command_killed("probe", 9, 0.0),
    )

    result = _run_governed(monkeypatch, td)

    assert result["structured_content"]["executed"] is False, result
    assert result["structured_content"]["error"] == "killed", result
    assert result["is_error"] is True, result
    assert "killed" in result["content"].lower(), result


def test_cli_adapter_raises_on_a_signal_kill(tmp_path, monkeypatch):
    """The adapter is one of the three paths #25 names."""
    from src.sdk.tools_core.cli_adapter import CLIToolAdapter

    class _CLI(CLIToolAdapter):
        cli_name = "firecrawl"
        install_hint = "npm i -g firecrawl"

    adapter = _CLI()
    monkeypatch.setattr(_CLI, "is_available", lambda self: True)
    killed = SandboxResult(exit_code=-9, stdout="", stderr="", signalled=True)
    monkeypatch.setattr(
        "src.sdk.sandbox.get_sandbox_backend",
        lambda: type("B", (), {"run": lambda *a, **k: killed})(),
    )

    with pytest.raises(CommandKilledError) as exc:
        adapter.run(["scrape", "https://example.com"])
    assert exc.value.signal_number == 9, exc.value
    # The label stays minimized: argv can carry secrets (review P2-1 on #24).
    assert exc.value.command == "firecrawl scrape", exc.value


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
def test_custom_command_tool_raises_on_a_signal_kill(tmp_path, mode):
    """The custom TOOL.md seam must not return a kill as a failed exit string.

    It calls subprocess directly, so a negative returncode is a real signal —
    the sandbox's synthetic -1 for its own timeout is why the seam there needs
    an explicit flag and this one does not.
    """
    import subprocess as sp

    if mode == "parsed":
        # The primary path: a TOOL.md parsed by runner.get_custom_tools.
        from src.sdk.tools_custom import _parse_tool_file

        tool_file = tmp_path / "TOOL.md"
        tool_file.write_text(
            "---\nname: killed_tool\ndescription: d\ncommand: echo hi\n---\n"
        )
        td = _parse_tool_file(tool_file)
        assert td is not None
    else:
        from src.sdk.tool_index import _rebuild_custom_function

        td = _rebuild_custom_function(
            ToolDefinition(name="killed_tool", description="d"),
            {"command": "echo hi", "install": [], "tool_dir": ""},
        )
    killed = sp.CompletedProcess(["echo", "hi"], returncode=-9, stdout="partial", stderr="")
    with patch("subprocess.run", side_effect=[sp.CompletedProcess([], 0, "", ""), killed]):
        with pytest.raises(CommandKilledError) as exc:
            td.function()
    assert exc.value.signal_number == 9, exc.value
