"""Issue #22: bounded results must be explicit and recoverable without reruns."""

import json
import subprocess
from unittest.mock import patch

import pytest

from src.sdk.tool_index import _rebuild_custom_function
from src.sdk.tools import ToolDefinition
from src.sdk.tools_custom import _parse_tool_file


@pytest.fixture
def result_scope(tmp_path, monkeypatch):
    from src.storage.paths import DataPaths

    def paths(user_id="default_user", workspace_id="personal"):
        return DataPaths(
            user_id=user_id, workspace_id=workspace_id,
            data_root=tmp_path / "data", data_path=tmp_path / "settings",
        )

    monkeypatch.setattr("src.storage.paths.get_paths", paths)
    return paths


def make_tool(tmp_path, mode, **scope):
    if mode == "reconstructed":
        return _rebuild_custom_function(
            ToolDefinition(name="fixture_output", description="Output fixture"),
            {"command": "echo fixture"}, **scope,
        )
    tool_file = tmp_path / "TOOL.md"
    tool_file.write_text(
        "---\nname: fixture_output\ndescription: Output fixture\ncommand: echo fixture\n---\n"
    )
    return _parse_tool_file(tool_file, **scope)


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
@pytest.mark.parametrize("size", [0, 4999, 5000, 5001, 6000])
def test_command_output_boundary_and_recovery(tmp_path, result_scope, mode, size):
    td = make_tool(tmp_path, mode)
    output = "A" * size
    if size > 5000:
        output += "END_OF_RESULT_MARKER"
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, "")) as run:
        value = td.function()
        if size <= 5000:
            assert value == (output or "(no output)")
            return
        envelope = json.loads(value)
        assert envelope["truncated"] is True
        assert envelope["total_chars"] == len(output)
        assert envelope["content"] == output[:5000]
        assert envelope["end"] == 5000
        from src.sdk.tools_core.tool_results import tool_result_read

        page = tool_result_read.function(envelope["result_id"], offset=5000).structured_content
        assert page["content"] == output[5000:]
        assert page["end"] == len(output)
        assert page["has_more"] is False
        assert sum(call.kwargs.get("shell", False) for call in run.call_args_list) == 1


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
def test_result_scope_is_bound_not_taken_from_command_arguments(tmp_path, result_scope, mode):
    td = make_tool(tmp_path, mode, user_id="alice", workspace_id="project-a")
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "z" * 6000, "")):
        envelope = json.loads(td.function(user_id="bob", workspace_id="project-b"))
    from src.sdk.tools_core.tool_results import tool_result_read

    result_id = envelope["result_id"]
    own = tool_result_read.function(result_id, user_id="alice", workspace_id="project-a").structured_content
    assert own["content"] == "z" * 5000
    for user, workspace in [("bob", "project-a"), ("alice", "project-b")]:
        denied = tool_result_read.function(result_id, user_id=user, workspace_id=workspace).structured_content
        assert "error" in denied
        assert "content" not in denied


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
def test_command_errors_and_timeouts_unchanged(tmp_path, result_scope, mode):
    td = make_tool(tmp_path, mode)
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 7, "oops", "!")):
        assert td.function() == "Command failed (exit 7):\noops!"
    with patch("subprocess.run", side_effect=[None, subprocess.TimeoutExpired("echo", 120)]):
        assert td.function() == "Command timed out after 120 seconds."


def test_real_command_large_output_round_trip(tmp_path, result_scope):
    from src.sdk.tools_core.tool_results import tool_result_read

    script = tmp_path / "emit.py"
    script.write_text("import sys; sys.stdout.write('abc🌍\\n' * 50000)")
    tool_file = tmp_path / "TOOL.md"
    import sys
    tool_file.write_text(
        "---\nname: large_output\ndescription: Large output fixture\n"
        f"command: '{sys.executable} {script}'\n---\n"
    )
    td = _parse_tool_file(tool_file)
    envelope = json.loads(td.function())
    content = envelope["content"]
    offset = envelope["end"]
    while True:
        page = tool_result_read.function(envelope["result_id"], offset=offset).structured_content
        content += page["content"]
        offset = page["end"]
        if not page["has_more"]:
            break
    assert content == "abc🌍\n" * 50000


def test_recovery_in_fresh_process_without_files_read(tmp_path, result_scope):
    import sys

    from src.sdk.tool_results import format_output

    envelope = json.loads(format_output("abc🌍\0" * 50000, "alice", "project-a"))
    script = tmp_path / "recover.py"
    script.write_text('''
import asyncio
import json
import sys
from pathlib import Path
import src.storage.paths as paths
from src.sdk.loop import AgentLoop
from src.sdk.messages import ToolCall
from src.sdk.native_tools import get_native_tools
from src.sdk.tools_custom import is_core_tool

root = Path(sys.argv[1])
paths.get_paths = lambda user_id, workspace_id: paths.DataPaths(
    user_id=user_id, workspace_id=workspace_id,
    data_root=root / "data", data_path=root / "settings",
)
reader = next(t for t in get_native_tools() if t.name == "tool_result_read")
assert is_core_tool(reader.name)
assert reader.annotations.read_only and not reader.annotations.destructive
loop = AgentLoop(provider=object(), tools=[reader], user_id="alice", workspace_id="project-a")
assert not loop._registry.has("files_read")
async def recover():
    content = ""
    offset = 0
    while True:
        result = await loop._execute_tool(ToolCall(
            id=str(offset), name="tool_result_read", arguments={
                "result_id": sys.argv[2], "offset": offset,
                "user_id": "bob", "workspace_id": "other",
            },
        ))
        page = json.loads(result.content)
        content += page["content"]
        offset = page["end"]
        if not page["has_more"]:
            break
    assert content == "abc🌍\\0" * 50000
asyncio.run(recover())
print("RECOVERED")
''')
    import os

    env = dict(os.environ, PYTHONPATH=str(__import__("pathlib").Path.cwd()))
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path), envelope["result_id"]],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RECOVERED" in result.stdout


@pytest.mark.parametrize("offset,limit", [(-1, 5), (0, 0), (0, 5001), (True, 1), (0, "5")])
def test_invalid_page_is_rejected(result_scope, offset, limit):
    from src.sdk.tool_results import format_output
    from src.sdk.tools_core.tool_results import tool_result_read

    saved = json.loads(format_output("x" * 6000, "alice", "personal"))
    page = tool_result_read.function(saved["result_id"], offset=offset, limit=limit, user_id="alice").structured_content
    assert "error" in page and "content" not in page


def test_save_failure_never_advertises_recovery(result_scope, monkeypatch):
    from src.sdk.tool_results import format_output

    def fail(*args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr("pathlib.Path.open", fail)
    result = json.loads(format_output("x" * 6000, "alice", "personal"))
    assert result["truncated"] is True
    assert result["content"] == "x" * 5000
    assert "not saved" in result["error"]
    assert "result_id" not in result


def test_result_missing_expired_and_bounds(result_scope, monkeypatch):
    from src.sdk import tool_results
    from src.sdk.tools_core.tool_results import tool_result_read

    now = tool_results.time.time()
    saved = json.loads(tool_results.format_output("x" * 6000, "alice", "personal"))
    result_id = saved["result_id"]
    for invalid in ["../secret", "0" * 32]:
        assert tool_result_read.function(invalid, user_id="alice").is_error
    assert tool_result_read.function(result_id, offset=6001, user_id="alice").is_error
    eof = tool_result_read.function(result_id, offset=6000, user_id="alice").structured_content
    assert eof["content"] == "" and eof["has_more"] is False
    monkeypatch.setattr(tool_results.time, "time", lambda: now + 8 * 86400)
    assert tool_result_read.function(result_id, user_id="alice").is_error


async def test_lazy_execution_binds_loop_scope(tmp_path, result_scope):
    from unittest.mock import MagicMock

    from src.sdk.loop import AgentLoop
    from src.sdk.messages import ToolCall
    from src.sdk.tools_core.tool_results import tool_result_read

    loop = AgentLoop(provider=object(), tools=[], user_id="alice", workspace_id="project-a")
    index = MagicMock()
    index.get_definition.return_value = ToolDefinition(name="fixture_output", description="Fixture")
    index.get_reconstruct.return_value = {"command": "echo fixture", "user_id": "bob"}
    index.get_tool_type.return_value = "custom"
    loop._tool_index = index
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "x" * 6000, "")):
        result = await loop._execute_tool(ToolCall(id="1", name="fixture_output", arguments={}))
    envelope = json.loads(result.content)
    page = tool_result_read.function(envelope["result_id"], user_id="alice", workspace_id="project-a")
    assert not page.is_error
    assert page.structured_content["content"] == "x" * 5000
    assert tool_result_read.function(envelope["result_id"]).is_error


async def test_reader_respects_own_capability_policy(result_scope):
    from src.sdk.loop import AgentLoop
    from src.sdk.messages import ToolCall
    from src.sdk.tools_core.tool_results import tool_result_read

    loop = AgentLoop(provider=object(), tools=[tool_result_read], user_id="alice")
    loop._caps_check = lambda name: name != "tool_result_read"
    result = await loop._execute_tool(ToolCall(
        id="1", name="tool_result_read", arguments={"result_id": "0" * 32},
    ))
    assert result.is_error and "disabled" in result.content


def test_discovery_binds_invoking_scope(tmp_path, result_scope):
    from src.sdk.tools_core.tool_results import tool_result_read
    from src.sdk.tools_custom import get_custom_tools

    tools_dir = result_scope("alice", "project-a").user_tools_dir() / "fixture"
    tools_dir.mkdir()
    make_tool(tools_dir, "parsed")
    td = next(t for t in get_custom_tools("alice", "project-a") if t.name == "fixture_output")
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "x" * 6000, "")):
        saved = json.loads(td.function())
    assert not tool_result_read.function(saved["result_id"], user_id="alice", workspace_id="project-a").is_error
    assert tool_result_read.function(saved["result_id"], user_id="alice", workspace_id="personal").is_error


def test_retention_evicts_oldest_results(result_scope):
    from src.sdk.tool_results import format_output
    from src.sdk.tools_core.tool_results import tool_result_read

    first = json.loads(format_output("x" * 6000, "alice", "personal"))
    for _ in range(100):
        last = json.loads(format_output("y" * 6000, "alice", "personal"))
    assert tool_result_read.function(first["result_id"], user_id="alice").is_error
    assert not tool_result_read.function(last["result_id"], user_id="alice").is_error


@pytest.mark.parametrize("user_id", ["alice", None])
async def test_missing_loop_context_uses_trusted_defaults(result_scope, user_id):
    from src.sdk.loop import AgentLoop
    from src.sdk.messages import ToolCall
    from src.sdk.tool_results import format_output
    from src.sdk.tools_core.tool_results import tool_result_read

    saved = json.loads(format_output("x" * 6000, user_id or "default_user", "personal"))
    loop = AgentLoop(provider=object(), tools=[tool_result_read], user_id=user_id)
    result = await loop._execute_tool(ToolCall(
        id="read", name="tool_result_read", arguments={
            "result_id": saved["result_id"], "offset": 5000,
            "user_id": "ignored", "workspace_id": "ignored",
        },
    ))
    assert not result.is_error, result.content
    assert result.structured_content["content"] == "x" * 1000


async def test_manager_reader_uses_manager_identity(result_scope, monkeypatch):
    from types import SimpleNamespace

    from src.sdk.loop import AgentLoop
    from src.sdk.messages import Message, ToolCall
    from src.sdk.tool_results import format_output
    from src.subagent.manager import SubagentManager

    saved = json.loads(format_output("manager" * 1000, "alice", "personal"))
    manager = SubagentManager("alice")
    monkeypatch.setattr("src.sdk.providers.factory.create_model_from_config", lambda _: object())
    monkeypatch.setattr("src.sdk.audit.ensure_audit_store_subscribed", lambda _: None)

    async def run(loop, messages):
        # Keep actual loop construction and tool execution; replace only LLM I/O.
        result = await loop._execute_tool(ToolCall(
            id="read", name="tool_result_read", arguments={"result_id": saved["result_id"]},
        ))
        assert not result.is_error, result.content
        assert result.structured_content["content"].startswith("manager")
        return SimpleNamespace(messages=[Message.assistant("recovered")])

    monkeypatch.setattr(AgentLoop, "run", run)
    result = await manager._invoke_async({"tools": ["tool_result_read"]}, "read saved output")
    assert result["output"] == "recovered"


def test_eviction_tolerates_vanished_entry(result_scope, monkeypatch):
    from pathlib import Path

    from src.sdk.tool_results import format_output
    from src.sdk.tools_core.tool_results import tool_result_read

    first = json.loads(format_output("x" * 6000, "alice", "personal"))
    original = Path.lstat

    def vanished(path, *args, **kwargs):
        if path.name == first["result_id"] + ".result":
            raise FileNotFoundError("concurrent eviction")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", vanished)
    saved = json.loads(format_output("y" * 6000, "alice", "personal"))
    assert "result_id" in saved, saved.get("error")
    assert not tool_result_read.function(saved["result_id"], user_id="alice").is_error
