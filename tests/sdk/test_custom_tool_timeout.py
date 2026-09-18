"""Issue #23: the command cap must be configurable and must never claim success."""

import subprocess
from unittest.mock import patch

import pytest

from src.sdk.tool_index import _rebuild_custom_function
from src.sdk.tools import ToolDefinition
from src.sdk.tools_custom import _parse_tool_file


def write_tool(tmp_path, annotations=""):
    tool_file = tmp_path / "TOOL.md"
    lines = ["---", "name: fixture_command", "description: Timeout fixture", "command: echo fixture"]
    if annotations:
        lines += ["annotations:", *annotations.rstrip("\n").split("\n")]
    lines.append("---")
    tool_file.write_text("\n".join(lines) + "\n")
    td = _parse_tool_file(tool_file)
    assert td is not None, tool_file.read_text()
    return td


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
@pytest.mark.parametrize(
    "declared,expected",
    [(None, 300.0), ("timeout_seconds: 42", 42.0), ("timeout_seconds: 7.5", 7.5), ("timeout_seconds: none", None)],
)
def test_declared_timeout_reaches_subprocess(tmp_path, mode, declared, expected):
    if mode == "reconstructed":
        annotations = {"timeout_seconds": expected} if declared is not None else {}
        td = ToolDefinition(name="fixture_command", description="d")
        td.annotations.timeout_seconds = annotations.get("timeout_seconds", 300.0)
        td = _rebuild_custom_function(td, {"command": "echo fixture"})
    else:
        block = "" if declared is None else f"  {declared}"
        td = write_tool(tmp_path, block)
    calls: list[float | None] = []

    def record(*args, **kwargs):
        calls.append(kwargs.get("timeout", "missing"))
        return subprocess.CompletedProcess([], 0, "ok", "")

    with patch("subprocess.run", side_effect=record):
        td.function()
    assert calls[1] == expected


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
def test_cap_kill_surfaces_as_failure_not_success(tmp_path, mode, monkeypatch):
    if mode == "reconstructed":
        td = _rebuild_custom_function(ToolDefinition(name="fixture_command", description="d"), {"command": "echo fixture"})
    else:
        td = write_tool(tmp_path)
    monkeypatch.setattr("src.sdk.tool_results.time.monotonic", lambda: 100.0)

    def kill(*args, **kwargs):
        if kwargs.get("shell"):
            raise subprocess.TimeoutExpired("echo fixture", 300)
        return subprocess.CompletedProcess([], 0, "", "")

    with patch("subprocess.run", side_effect=kill):
        with pytest.raises(subprocess.TimeoutExpired) as exc:
            td.function()
    assert "timed_out" in exc.value.output


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
@pytest.mark.parametrize("declared,value", [("  timeout_seconds: 0\n", 0), ("  timeout_seconds: -5\n", -5), ('  timeout_seconds: soon\n', "soon")])
def test_invalid_timeout_is_rejected_not_treated_as_unbounded(tmp_path, mode, declared, value):
    if mode == "reconstructed":
        td = _rebuild_custom_function(ToolDefinition(name="fixture_command", description="d"), {"command": "echo fixture"})
        with pytest.raises(ValueError, match="timeout_seconds"):
            td.annotations.timeout_seconds = value
        return
    with pytest.raises(ValueError, match="timeout_seconds"):
        write_tool(tmp_path, declared.rstrip("\n"))


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
def test_timeout_raises_distinct_failure_with_elapsed(tmp_path, mode, monkeypatch):
    if mode == "reconstructed":
        td = _rebuild_custom_function(ToolDefinition(name="fixture_command", description="d"), {"command": "echo fixture"})
    else:
        td = write_tool(tmp_path)
    ticks = iter([100.0, 142.5])
    monkeypatch.setattr("src.sdk.tool_results.time.monotonic", lambda: next(ticks))
    with patch("subprocess.run", side_effect=[None, subprocess.TimeoutExpired("echo", 300)]):
        with pytest.raises(subprocess.TimeoutExpired) as exc:
            td.function()
    detail = exc.value.output
    assert "timed_out" in detail
    assert "42.5" in detail
    assert "300" in detail


def test_governance_marks_timed_out_as_not_executed(tmp_path, monkeypatch):
    import asyncio

    import src.storage.paths as paths_mod
    from src.sdk.governance import GovernanceService
    from src.sdk.tools import ToolDefinition

    monkeypatch.setattr(
        paths_mod.DataPaths, "root", property(lambda self: tmp_path / "root")
    )
    import src.sdk.governance as gov

    monkeypatch.setattr(gov, "_services", {})
    svc = GovernanceService()
    user = "timeout_user"
    monkeypatch.setattr(svc, "resolve_tier", lambda *_: "explicit")
    monkeypatch.setattr(svc, "_log_execution_result", lambda *_: None)
    monkeypatch.setattr(svc, "_emit_receipt", lambda *a, **k: None)

    async def killed(*_args, **_kwargs):
        from src.sdk.tool_results import raise_command_timeout

        raise_command_timeout("curl", 300.0, 0.0)

    td = ToolDefinition(name="fixture_command", description="d", function=killed)

    proposal_id = svc.create_pending(user, "fixture_command", {}, tier="explicit")
    svc.approve(user, proposal_id)

    result = asyncio.run(svc.execute_approved(user, proposal_id, registry=[td]))
    assert result["structured_content"]["executed"] is False, result
    assert result["structured_content"]["error"] == "timed_out", result
    assert result["is_error"] is True


@pytest.mark.parametrize("value", [True, False, float("inf"), float("nan"), 0, -1, "soon", "30s"])
def test_malformed_timeout_is_rejected(value):
    """A value that would silently change the cap's meaning must raise."""
    from src.sdk.tools import ToolAnnotations

    with pytest.raises(Exception, match="timeout_seconds"):
        ToolAnnotations(timeout_seconds=value)


def test_boolean_true_is_not_a_one_second_cap():
    """Regression for the review F4 finding: YAML `true` was lax-coerced to 1.0."""
    from src.sdk.tools import DEFAULT_COMMAND_TIMEOUT_SECONDS, ToolAnnotations

    assert ToolAnnotations().timeout_seconds == DEFAULT_COMMAND_TIMEOUT_SECONDS
    with pytest.raises(Exception, match="timeout_seconds"):
        ToolAnnotations(timeout_seconds=True)


def test_index_round_trip_preserves_declared_timeout(tmp_path):
    """F5: a non-default value must survive index_tool -> get_definition -> rebuild."""
    from src.sdk.tool_index import ToolIndex, _rebuild_custom_function

    td = write_tool(tmp_path, "  timeout_seconds: 42")
    index = ToolIndex(tmp_path / "index")
    try:
        index.index_tool(td, tool_type="custom", reconstruct={"command": "echo fixture"})
        restored = _rebuild_custom_function(index.get_definition(td.name), index.get_reconstruct(td.name))
    finally:
        index.close()
    calls = []

    def record(*args, **kwargs):
        calls.append(kwargs.get("timeout", "missing"))
        return subprocess.CompletedProcess([], 0, "ok", "")

    with patch("subprocess.run", side_effect=record):
        restored.function()
    assert calls[1] == 42.0


def test_malformed_tool_is_skipped_not_session_fatal(tmp_path):
    """F3: one bad TOOL.md skips that tool only; discovery still returns the rest."""
    from src.sdk.tools_custom import scan_tools_dir

    good_dir = tmp_path / "good"
    good_dir.mkdir()
    write_tool(good_dir)
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "TOOL.md").write_text(
        "---\nname: fixture_command\ndescription: d\ncommand: echo fixture\n"
        "annotations:\n  timeout_seconds: 30s\n---\n"
    )
    tools = scan_tools_dir(tmp_path)
    assert [t.name for t in tools] == ["fixture_command"]
