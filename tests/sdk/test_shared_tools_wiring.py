"""Issue #27: the deployment-shared tools dir must be wired into the session index.

`paths.workspace_tools_dir()` (`data_root/Tools`) is documented as the
deployment-shared tools source, and `get_custom_tools()` honours it. But the
session runner passed `workspace_tools_dir=None` into the persisted tool index,
so shared tool sources were never hashed (no reindex on change) and shared-only
tools were indexed with an empty `reconstruct` blob — which rebuilt as a tool
that silently returned "(no output)" instead of running.

These tests use a non-default user on purpose: for `default_user`,
`user_tools_dir()` and `workspace_tools_dir()` are the same directory, which
hides the defect by letting the per-user lookup find the shared file.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.sdk.tool_index import (
    _rebuild_custom_function,
    compute_source_hashes,
    get_or_create_index,
)
from src.sdk.tools import ToolDefinition
from src.storage.paths import DataPaths

USER = "alice"
SHARED_TOOL = """---
name: shared_echo
description: Shared tool fixture
command: echo shared-fixture
---
"""


def _paths(data_root: Path) -> DataPaths:
    return DataPaths(user_id=USER, workspace_id="personal", data_root=str(data_root))


def _write_shared_tool(data_root: Path) -> Path:
    """Create the shared tool under `<data_root>/Tools/shared_echo`."""
    tool_dir = _paths(data_root).workspace_tools_dir() / "shared_echo"
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "TOOL.md").write_text(SHARED_TOOL)
    return tool_dir


def _mcp_config(data_root: Path) -> Path:
    path = data_root / ".mcp.json"
    path.write_text("{}")
    return path


# --- index layer -----------------------------------------------------------


def test_shared_dir_is_distinct_from_user_dir(tmp_path):
    """Guard the fixture assumption the rest of these tests depend on."""
    paths = _paths(tmp_path)
    assert paths.user_tools_dir() != paths.workspace_tools_dir()


def test_shared_sources_are_hashed_when_the_dir_is_supplied(tmp_path):
    _write_shared_tool(tmp_path)
    paths = _paths(tmp_path)

    hashes = compute_source_hashes(
        paths.user_tools_dir(), paths.workspace_tools_dir(), _mcp_config(tmp_path)
    )
    assert "workspace:shared_echo" in hashes, sorted(hashes)


def test_shared_sources_are_absent_when_the_dir_is_omitted(tmp_path):
    """The runner's `None` is what produced the reported symptom."""
    _write_shared_tool(tmp_path)
    paths = _paths(tmp_path)

    hashes = compute_source_hashes(paths.user_tools_dir(), None, _mcp_config(tmp_path))
    assert not any(k.startswith("workspace:") for k in hashes), sorted(hashes)


def test_shared_tool_change_triggers_reindex(tmp_path):
    tool_dir = _write_shared_tool(tmp_path)
    paths = _paths(tmp_path)

    before = compute_source_hashes(
        paths.user_tools_dir(), paths.workspace_tools_dir(), _mcp_config(tmp_path)
    )
    (tool_dir / "TOOL.md").write_text(SHARED_TOOL.replace("shared-fixture", "changed"))
    after = compute_source_hashes(
        paths.user_tools_dir(), paths.workspace_tools_dir(), _mcp_config(tmp_path)
    )
    assert before != after


def test_indexed_shared_tool_keeps_its_reconstruct(tmp_path):
    _write_shared_tool(tmp_path)
    paths = _paths(tmp_path)

    idx, _commit = get_or_create_index(
        paths.user_tools_dir(), paths.workspace_tools_dir(), _mcp_config(tmp_path),
        user_id=USER, workspace_id="personal",
    )
    try:
        from src.sdk.tools_custom import find_tool_file, load_tool_meta

        tool_file = find_tool_file("shared_echo", paths.user_tools_dir(), paths.workspace_tools_dir())
        assert tool_file is not None
        meta = load_tool_meta(tool_file)
        assert meta and meta.get("command")
        idx.index_tool(
            ToolDefinition(name="shared_echo", description="d"),
            tool_type="custom",
            reconstruct={
                "command": meta["command"],
                "install": [],
                "tool_dir": str(tool_file.parent),
            },
        )
        assert idx.get_reconstruct("shared_echo")["command"] == "echo shared-fixture"
    finally:
        idx.close()


# --- runner wiring ---------------------------------------------------------


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    """Point user data at tmp_path and stub only what create_sdk_loop needs."""
    from src.sdk import runner
    from src.storage import paths as paths_mod

    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(data_root))
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path / "settings"))
    monkeypatch.setattr(paths_mod.DataPaths, "root", property(lambda self: data_root))
    settings = MagicMock()
    settings.memory.summarization.enabled = False
    settings.memory.summarization.model = None
    settings.verification.enabled = False
    settings.langfuse.enabled = False
    settings.agent.model = "openai:gpt-4.1"
    monkeypatch.setattr(runner, "get_settings", lambda: settings)
    monkeypatch.setattr(runner, "get_native_tools", lambda: [])
    monkeypatch.setattr(runner, "_seed_default_workspace", lambda: None)
    monkeypatch.setattr(runner, "_get_system_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(
        "src.config.user_settings_service.load_saved_user_settings", lambda user_id: None
    )
    provider = AsyncMock()
    provider.provider_id = "openai"
    provider.model = "gpt-4.1"
    monkeypatch.setattr(runner, "get_cached_model_provider", lambda *a, **k: provider)
    return data_root


@pytest.mark.asyncio
async def test_session_index_includes_shared_tool_reconstruct(session_env):
    """End-to-end: a session build must index the shared tool with its command."""
    from src.sdk import runner
    from src.sdk.tool_index import ToolIndex

    _write_shared_tool(session_env)
    await runner.create_sdk_loop(user_id=USER, session_id="s-27")

    paths = _paths(session_env)
    idx = ToolIndex(paths.user_tools_dir() / ".index")
    try:
        assert "shared_echo" in idx.list_all_names(), "shared tool was never indexed"
        reconstruct = idx.get_reconstruct("shared_echo")
        assert reconstruct.get("command") == "echo shared-fixture", (
            f"shared tool indexed without its command: {reconstruct}"
        )
    finally:
        idx.close()


@pytest.mark.asyncio
async def test_session_index_hashes_shared_sources(session_env):
    """The persisted hash set must track shared sources, not only per-user ones."""
    from src.sdk import runner

    _write_shared_tool(session_env)
    await runner.create_sdk_loop(user_id=USER, session_id="s-27b")

    hashes_path = _paths(session_env).user_tools_dir() / ".index" / ".index_hashes.json"
    assert hashes_path.exists(), "index hash file was not persisted"
    hashes = json.loads(hashes_path.read_text())
    assert any(k.startswith("workspace:") for k in hashes), sorted(hashes)


# --- defensive guard -------------------------------------------------------


def test_rebuild_without_a_command_fails_loudly():
    """An empty reconstruct must not masquerade as a successful no-op run."""
    td = _rebuild_custom_function(
        ToolDefinition(name="shared_echo", description="d"),
        {"command": "", "install": [], "tool_dir": ""},
    )
    result = None
    try:
        result = td.function()
    except Exception:
        return
    pytest.fail(f"empty-command rebuild returned {result!r} instead of failing")


def test_rebuild_with_a_command_still_runs():
    """The guard must not disturb the normal rebuild path."""
    td = _rebuild_custom_function(
        ToolDefinition(name="shared_echo", description="d"),
        {"command": "echo ok", "install": [], "tool_dir": ""},
    )
    assert td.function().strip() == "ok"
