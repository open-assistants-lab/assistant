"""Issue #28: tool_reload must not drop native rows it cannot restore.

`tool_reload` clears the persisted tool index and re-indexes only custom
(`TOOL.md`) and MCP rows. The session runner re-indexes only when the index is
empty, and `tool_reload` commits the source hashes, so nothing ever rebuilds
the native rows it removed. Non-core native tools are reachable only through
their index row, so after a reload they become permanently unresolvable:
`tool_search` cannot advertise them and lazy load reports "Unknown tool".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.sdk.tool_index import ToolIndex
from src.sdk.tools import ToolDefinition
from src.storage.paths import DataPaths

USER = "bob"


def _paths() -> DataPaths:
    return DataPaths(user_id=USER, workspace_id="personal")


@pytest.fixture(autouse=True)
def isolate_root(tmp_path, monkeypatch):
    """Keep DataPaths and capability writes inside tmp_path."""
    from src.storage import paths as paths_mod

    root = tmp_path / "data"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(paths_mod.DataPaths, "root", property(lambda self: root))
    import src.sdk.capabilities as caps_mod

    monkeypatch.setattr(caps_mod, "user_capabilities_root", lambda user_id: root / user_id)
    return root


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
    monkeypatch.setattr(runner, "_seed_default_workspace", lambda: None)
    monkeypatch.setattr(runner, "_get_system_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(
        "src.config.user_settings_service.load_saved_user_settings", lambda user_id: None
    )
    provider = AsyncMock()
    provider.provider_id = "openai"
    provider.model = "gpt-4.1"
    monkeypatch.setattr(runner, "get_cached_model_provider", lambda *a, **k: provider)

    # One non-core native tool (only reachable via its index row) and one
    # custom tool, so the reload leaves a non-empty index behind.
    native = ToolDefinition(name="files_list", description="List files", function=lambda **_: "ok")
    custom_dir = _paths().user_tools_dir() / "user_only"
    custom_dir.mkdir(parents=True, exist_ok=True)
    (custom_dir / "TOOL.md").write_text(
        "---\nname: user_only\ndescription: Per-user fixture\ncommand: echo user-only\n---\n"
    )
    monkeypatch.setattr(runner, "get_native_tools", lambda: [native])
    monkeypatch.setattr("src.sdk.native_tools.get_native_tools", lambda: [native])
    return native


def _index_names() -> set[str]:
    idx = ToolIndex(_paths().user_tools_dir() / ".index")
    try:
        return set(idx.list_all_names())
    finally:
        idx.close()


@pytest.mark.asyncio
async def test_reload_keeps_native_rows(session_env):
    """A reload must not silently drop native tools it never re-indexes."""
    from src.sdk import runner
    from src.sdk.loop import _current_agent_loop
    from src.sdk.tools_core.tool_reload import tool_reload

    loop = await runner.create_sdk_loop(user_id=USER, session_id="s-28")
    before = _index_names()
    assert "files_list" in before, f"precondition: native row indexed, got {sorted(before)}"
    assert "user_only" in before, f"precondition: custom row indexed, got {sorted(before)}"

    token = _current_agent_loop.set(loop)
    try:
        result = tool_reload.function()
    finally:
        _current_agent_loop.reset(token)
    assert "Error" not in result, result

    after = _index_names()
    assert "user_only" in after, f"custom row should survive the reload: {sorted(after)}"
    assert "files_list" in after, (
        f"reload dropped the native row and nothing restores it: {sorted(after)}"
    )


@pytest.mark.asyncio
async def test_reloaded_native_tool_is_still_resolvable(session_env):
    """The user-visible symptom: a dropped native row means 'Unknown tool'."""
    from src.sdk import runner
    from src.sdk.loop import _current_agent_loop
    from src.sdk.messages import ToolCall
    from src.sdk.tools_core.tool_reload import tool_reload

    loop = await runner.create_sdk_loop(user_id=USER, session_id="s-28b")
    token = _current_agent_loop.set(loop)
    try:
        tool_reload.function()
    finally:
        _current_agent_loop.reset(token)

    resolved = await loop._try_lazy_load(
        ToolCall(id="1", name="files_list", arguments={})
    )
    assert resolved is not None, "native tool unresolvable after tool_reload"
    assert not resolved.is_error, resolved.content
