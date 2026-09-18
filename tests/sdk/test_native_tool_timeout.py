"""Issue #24: native command tools must not report a cap-killed run as executed."""

import asyncio
import subprocess

import pytest

from src.sdk.sandbox import SandboxLimits, SandboxResult
from src.sdk.tools_core.cli_adapter import CLIToolAdapter


def _timed_out(stdout=""):
    return SandboxResult(exit_code=-1, stdout=stdout, stderr="", timed_out=True)


def _prepare(tool_name, tmp_path, monkeypatch, result):
    """Point the tool at tmp storage and stub the sandbox transport."""
    import src.sdk.tools_core.code_execute as ce
    import src.sdk.tools_core.shell as shell

    if tool_name == "shell_execute":
        monkeypatch.setattr(shell, "_get_root_path", lambda *a, **k: tmp_path)
        td, args = shell.shell_execute, {"command": "echo hi", "user_id": "u"}
    else:
        monkeypatch.setattr(ce, "_workspace_root", lambda *a, **k: tmp_path)
        monkeypatch.setattr(ce, "_get_limits", lambda: SandboxLimits(timeout_seconds=30.0))
        td, args = ce.code_execute, {"code": "print(1)", "user_id": "u"}
    backend = type(
        "B",
        (),
        {
            "run": lambda *a, **k: result,
            "validate_source": lambda *a, **k: None,
            "validate_write_path": lambda *a, **k: None,
        },
    )()
    # code_execute binds get_sandbox_backend at import time (AGENTS.md
    # pitfall: patch the module attribute, not the defining module).
    monkeypatch.setattr("src.sdk.sandbox.get_sandbox_backend", lambda: backend)
    monkeypatch.setattr(ce, "get_sandbox_backend", lambda: backend, raising=False)
    return td, args


@pytest.mark.parametrize("tool_name", ["shell_execute", "code_execute"])
def test_native_command_timeout_raises_not_returns(tool_name, tmp_path, monkeypatch):

    td, args = _prepare(tool_name, tmp_path, monkeypatch, _timed_out())

    with pytest.raises(subprocess.TimeoutExpired) as exc:
        td.function(**args)
    assert "timed_out" in exc.value.output


def test_cli_adapter_timeout_raises(tmp_path, monkeypatch):
    class _CLI(CLIToolAdapter):
        cli_name = "firecrawl"
        install_hint = "npm i -g firecrawl"

    adapter = _CLI()
    monkeypatch.setattr(_CLI, "is_available", lambda self: True)
    monkeypatch.setattr(
        "src.sdk.sandbox.get_sandbox_backend",
        lambda: type("B", (), {"run": lambda *a, **k: _timed_out()})(),
    )
    import src.sdk.tools_core.cli_adapter as ca

    monkeypatch.setattr(ca, "get_sandbox_backend", lambda: type("B", (), {"run": lambda *a, **k: _timed_out()})(), raising=False)
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        adapter.run(["scrape", "https://example.com"])
    assert "timed_out" in exc.value.output


def test_non_timeout_failures_keep_their_shapes(tmp_path, monkeypatch):
    import src.sdk.tools_core.shell as shell

    monkeypatch.setattr(shell, "_get_root_path", lambda *a, **k: tmp_path)
    monkeypatch.setattr(
        "src.sdk.sandbox.get_sandbox_backend",
        lambda: type("B", (), {"run": lambda *a, **k: SandboxResult(exit_code=3, stdout="", stderr="boom")})(),
    )
    value = shell.shell_execute.function("echo hi", user_id="u")
    assert "STDERR: boom" in value
    assert not isinstance(value, subprocess.TimeoutExpired)


@pytest.mark.parametrize("tool_name", ["shell_execute", "code_execute"])
def test_governed_native_timeout_records_not_executed(tool_name, tmp_path, monkeypatch):
    """End-to-end: the real governance executor must record executed: false."""
    import src.storage.paths as paths_mod
    from src.sdk.governance import GovernanceService

    td, args = _prepare(tool_name, tmp_path, monkeypatch, _timed_out())

    monkeypatch.setattr(
        paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
    )
    # code_execute is a professional-service tool (disabled by default), so
    # enable it explicitly: the contract under test is the timeout receipt.
    import src.sdk.capabilities as caps_mod

    caps_root = tmp_path / "caps"
    caps_root.mkdir(parents=True, exist_ok=True)
    (caps_root / "capabilities.yaml").write_text(
        "tools:\n  code_execute: true\n"
    )
    monkeypatch.setattr(caps_mod, "user_capabilities_root", lambda *a, **k: caps_root)
    import src.sdk.governance as gov

    monkeypatch.setattr(gov, "_services", {})
    svc = GovernanceService()
    user = "timeout_user"
    monkeypatch.setattr(svc, "resolve_tier", lambda *_: "explicit")
    monkeypatch.setattr(svc, "_log_execution_result", lambda *_: None)
    monkeypatch.setattr(svc, "_emit_receipt", lambda *a, **k: None)

    proposal_id = svc.create_pending(user, td.name, args, tier="explicit")
    svc.approve(user, proposal_id)
    result = asyncio.run(svc.execute_approved(user, proposal_id, registry=[td]))
    assert result["structured_content"]["executed"] is False, result
    assert result["structured_content"]["error"] == "timed_out", result
    assert result["is_error"] is True
