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
    needs_rebuild,
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


@pytest.fixture(autouse=True)
def isolate_root(tmp_path, monkeypatch) -> Path:
    """Resolve every DataPaths under tmp_path for this whole file.

    `get_or_create_index` derives its index dir from the module-level
    `get_paths`, so a test that only passes explicit directories would still
    write its index into the developer's real `~/Assistant`. Patch the root so
    both routes agree.
    """
    from src.storage import paths as paths_mod

    root = tmp_path / "data"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(paths_mod.DataPaths, "root", property(lambda self: root))
    # Capability writes resolve through the settings singleton, not DataPaths,
    # so toggle_tool would otherwise persist into the checkout's data/ dir.
    import src.sdk.capabilities as caps_mod

    monkeypatch.setattr(caps_mod, "user_capabilities_root", lambda user_id: root / user_id)
    return root


def _paths() -> DataPaths:
    return DataPaths(user_id=USER, workspace_id="personal")


def _write_shared_tool() -> Path:
    """Create the shared tool under `<data_root>/Tools/shared_echo`."""
    tool_dir = _paths().workspace_tools_dir() / "shared_echo"
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "TOOL.md").write_text(SHARED_TOOL)
    return tool_dir


def _write_user_tool() -> None:
    """A per-user tool so the index holds more than a single row."""
    tool_dir = _paths().user_tools_dir() / "user_only"
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "TOOL.md").write_text(
        "---\nname: user_only\ndescription: Per-user fixture\ncommand: echo user-only\n---\n"
    )


def _mcp_config() -> Path:
    path = _paths().user_mcp_config()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    return path


def _index_names() -> set[str]:
    from src.sdk.tool_index import ToolIndex

    idx = ToolIndex(_paths().user_tools_dir() / ".index")
    try:
        return set(idx.list_all_names())
    finally:
        idx.close()


# --- index layer -----------------------------------------------------------


def test_shared_dir_is_distinct_from_user_dir():
    """Guard the fixture assumption the rest of these tests depend on."""
    paths = _paths()
    assert paths.user_tools_dir() != paths.workspace_tools_dir()


def test_shared_sources_are_hashed_when_the_dir_is_supplied():
    _write_shared_tool()
    paths = _paths()

    hashes = compute_source_hashes(
        paths.user_tools_dir(), paths.workspace_tools_dir(), _mcp_config()
    )
    assert "workspace:shared_echo" in hashes, sorted(hashes)


def test_shared_sources_are_absent_when_the_dir_is_omitted():
    """The runner's `None` is what produced the reported symptom."""
    _write_shared_tool()
    paths = _paths()

    hashes = compute_source_hashes(paths.user_tools_dir(), None, _mcp_config())
    assert not any(k.startswith("workspace:") for k in hashes), sorted(hashes)


def test_shared_tool_change_triggers_reindex():
    tool_dir = _write_shared_tool()
    paths = _paths()

    before = compute_source_hashes(
        paths.user_tools_dir(), paths.workspace_tools_dir(), _mcp_config()
    )
    (tool_dir / "TOOL.md").write_text(SHARED_TOOL.replace("shared-fixture", "changed"))
    after = compute_source_hashes(
        paths.user_tools_dir(), paths.workspace_tools_dir(), _mcp_config()
    )
    assert before != after


def test_indexed_shared_tool_keeps_its_reconstruct():
    _write_shared_tool()
    paths = _paths()

    idx, _commit = get_or_create_index(
        paths.user_tools_dir(), paths.workspace_tools_dir(), _mcp_config(),
        user_id=USER, workspace_id="personal",
    )
    try:
        from src.sdk.tools_custom import find_tool_file, load_tool_meta

        tool_file = find_tool_file(
            "shared_echo", paths.user_tools_dir(), paths.workspace_tools_dir()
        )
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


def test_index_bookkeeping_does_not_invalidate_its_own_hashes():
    """`Tools/.index` is bookkeeping, not a tool source.

    Counting it made the hash set depend on whether the index already existed:
    the first commit (written before `.index` was created) could never match a
    later call, so `check_needs_reindex` reported a change and the next caller
    cleared every row.
    """
    _write_shared_tool()
    paths = _paths()
    mcp_config = _mcp_config()
    index_dir = paths.user_tools_dir() / ".index"

    idx, commit = get_or_create_index(
        paths.user_tools_dir(), paths.workspace_tools_dir(), mcp_config,
        user_id=USER, workspace_id="personal",
    )
    idx.index_tool(
        ToolDefinition(name="shared_echo", description="d"),
        tool_type="custom",
        reconstruct={"command": "echo shared-fixture"},
    )
    commit()
    idx.close()

    assert index_dir.exists(), "precondition: index dir now exists on disk"
    assert (
        needs_rebuild(
            paths.user_tools_dir(), paths.workspace_tools_dir(), mcp_config, index_dir
        )
        is False
    ), "index invalidated itself as soon as it existed"


def test_user_tool_wins_over_a_same_name_shared_tool():
    """find_tool_file must resolve the per-user copy first.

    get_custom_tools() merges shared-then-user, so the per-user file is the one
    the model is shown; resolving the shared file first made the runner record a
    command that did not match the tool's description.
    """
    _write_shared_tool()
    user_dir = _paths().user_tools_dir() / "shared_echo"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "TOOL.md").write_text(
        "---\nname: shared_echo\ndescription: User override\ncommand: echo user-version\n---\n"
    )

    from src.sdk.tools_custom import find_tool_file, load_tool_meta

    paths = _paths()
    found = find_tool_file(
        "shared_echo", paths.user_tools_dir(), paths.workspace_tools_dir()
    )
    assert found is not None
    assert found.parent.parent == paths.user_tools_dir(), found
    meta = load_tool_meta(found)
    assert meta and meta["command"] == "echo user-version"


# --- runner wiring ---------------------------------------------------------


@pytest.fixture
def session_env(isolate_root, monkeypatch):
    """Stub only what create_sdk_loop needs beyond the isolated root."""
    import os

    from src.sdk import runner

    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(isolate_root))
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(isolate_root / "settings"))
    os.makedirs(isolate_root / "settings", exist_ok=True)
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


@pytest.mark.asyncio
async def test_session_index_includes_shared_tool_reconstruct(session_env):
    """End-to-end: a session build must index the shared tool with its command."""
    from src.sdk import runner
    from src.sdk.tool_index import ToolIndex

    _write_shared_tool()
    await runner.create_sdk_loop(user_id=USER, session_id="s-27")

    idx = ToolIndex(_paths().user_tools_dir() / ".index")
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

    _write_shared_tool()
    await runner.create_sdk_loop(user_id=USER, session_id="s-27b")

    hashes_path = _paths().user_tools_dir() / ".index" / ".index_hashes.json"
    assert hashes_path.exists(), "index hash file was not persisted"
    hashes = json.loads(hashes_path.read_text())
    assert any(k.startswith("workspace:") for k in hashes), sorted(hashes)


@pytest.mark.asyncio
async def test_disabling_then_re_enabling_a_native_tool_restores_it(session_env, monkeypatch):
    """Disable must not destroy a row the enable path never restores.

    Non-core native tools are only reachable through their index row, so a
    one-way purge leaves them permanently "Unknown tool" — a restart does not
    help (hashes still match) and tool_reload does not re-index native rows.
    Capability-disabled rows are already filtered out of tool_search by
    resource_enabled(), and execution is blocked by the caps check, so the row
    does not need to be destroyed.
    """
    from src.http.routers import tools as tools_router
    from src.sdk import runner
    from src.sdk.messages import ToolCall
    from src.sdk.tools import ToolDefinition

    # A non-core native tool: CORE_TOOL_NAMES would be registered eagerly.
    native = ToolDefinition(name="files_list", description="List files", function=lambda **_: "ok")
    monkeypatch.setattr(runner, "get_native_tools", lambda: [native])
    # The router resolves its registry through a function-local import.
    monkeypatch.setattr("src.sdk.native_tools.get_native_tools", lambda: [native])

    loop = await runner.create_sdk_loop(user_id=USER, session_id="s-27d")
    assert "files_list" in _index_names(), "precondition: native tool indexed"

    # Drive the real endpoint: scope=none, then back to scope=all.
    await tools_router.toggle_tool(
        name="files_list", body={"scope": "none"}, user_id=USER, workspace_id="personal"
    )
    await tools_router.toggle_tool(
        name="files_list", body={"scope": "all"}, user_id=USER, workspace_id="personal"
    )

    assert "files_list" in _index_names(), (
        "re-enabling left the row missing: disable removed it and enable never restores it"
    )
    resolved = await loop._try_lazy_load(
        ToolCall(id="1", name="files_list", arguments={})
    )
    assert resolved is not None, "disabled-then-enabled tool stayed unresolvable"
    assert not resolved.is_error, resolved.content


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
