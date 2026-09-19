"""Issue #29: index integrity must come from row presence, not a count or a hash.

The runner re-indexed only when `idx.count() == 0` and `get_or_create_index` only
when the source hashes changed. Neither can tell whether the rows that *should*
exist are actually present, which left three repair gaps:

1. an install whose rows were dropped (matching hashes, non-empty index) was
   never rebuilt;
2. a crash between `tool_reload`'s clear and its re-index left a partial index
   whose hashes still matched;
3. `disable -> tool_reload -> re-enable` left no row, because the reload mirrors
   the index-time capability filter while the enable path never re-indexes.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.sdk.tool_index import ToolIndex
from src.sdk.tools import ToolDefinition
from src.storage.paths import DataPaths

USER = "carol"


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
    """One non-core native tool plus one custom tool, isolated to tmp_path."""
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

    native = ToolDefinition(
        name="files_list", description="List files", function=lambda **_: "ok"
    )
    custom_dir = _paths().user_tools_dir() / "user_only"
    custom_dir.mkdir(parents=True, exist_ok=True)
    (custom_dir / "TOOL.md").write_text(
        "---\nname: user_only\ndescription: Per-user fixture\ncommand: echo user-only\n---\n"
    )
    monkeypatch.setattr(runner, "get_native_tools", lambda: [native])
    monkeypatch.setattr("src.sdk.native_tools.get_native_tools", lambda: [native])
    return native


def _index() -> ToolIndex:
    return ToolIndex(_paths().user_tools_dir() / ".index")


def _names() -> set[str]:
    idx = _index()
    try:
        return set(idx.list_all_names())
    finally:
        idx.close()


def _hashes() -> dict:
    path = _paths().user_tools_dir() / ".index" / ".index_hashes.json"
    return json.loads(path.read_text()) if path.exists() else {}


async def _build(session_id: str):
    from src.sdk import runner

    return await runner.create_sdk_loop(user_id=USER, session_id=session_id)


def _reload(loop) -> str:
    from src.sdk.loop import _current_agent_loop
    from src.sdk.tools_core.tool_reload import tool_reload

    token = _current_agent_loop.set(loop)
    try:
        return tool_reload.function()
    finally:
        _current_agent_loop.reset(token)


@pytest.mark.asyncio
async def test_damaged_index_is_repaired_by_the_next_session_build(session_env):
    """A dropped row must be restored without a manual tool_reload.

    This is the install-damage and crash-window case at once: the index is left
    partial with the source hashes still matching, which the old
    `idx.count() == 0` gate could not detect.
    """
    await _build("s-29a")
    assert {"files_list", "user_only"} <= _names(), sorted(_names())
    hashes_before = _hashes()

    idx = _index()
    try:
        idx.remove_tool("files_list")  # simulate damage / partial re-index
    finally:
        idx.close()

    after_damage = _names()
    assert "files_list" not in after_damage
    assert after_damage, "precondition: index is NOT empty, so count()==0 cannot catch this"
    assert _hashes() == hashes_before, "precondition: hashes still match the sources"

    await _build("s-29b")
    assert "files_list" in _names(), (
        f"missing row was never restored: {sorted(_names())}"
    )


@pytest.mark.asyncio
async def test_healthy_index_is_not_rebuilt(session_env, monkeypatch):
    """The presence check must not thrash: a complete index is left alone."""
    await _build("s-29c")

    indexed: list[str] = []
    consulted = 0
    original = ToolIndex.index_tool
    original_names = ToolIndex.list_all_names

    def counting(self, td, tool_type, namespace="", reconstruct=None):
        indexed.append(td.name)
        return original(
            self, td, tool_type, namespace=namespace, reconstruct=reconstruct
        )

    def counting_names(self):
        nonlocal consulted
        consulted += 1
        return original_names(self)

    monkeypatch.setattr(ToolIndex, "index_tool", counting)
    monkeypatch.setattr(ToolIndex, "list_all_names", counting_names)
    await _build("s-29d")

    assert consulted > 0, "the presence check never consulted the index"
    assert indexed == [], f"healthy index was re-indexed: {sorted(indexed)}"


@pytest.mark.asyncio
async def test_disable_reload_enable_restores_the_tool(session_env):
    """disable -> tool_reload -> re-enable must leave a usable tool.

    The reload mirrors the index-time capability filter, so the disabled tool
    gets no row; re-enabling has to be repaired by the next build rather than
    waiting for another manual reload.
    """
    from src.http.routers import tools as tools_router
    from src.sdk.messages import ToolCall

    loop = await _build("s-29e")
    assert "files_list" in _names()

    await tools_router.toggle_tool(
        name="files_list", body={"scope": "none"}, user_id=USER, workspace_id="personal"
    )
    _reload(loop)
    await tools_router.toggle_tool(
        name="files_list", body={"scope": "all"}, user_id=USER, workspace_id="personal"
    )

    fresh = await _build("s-29f")
    assert "files_list" in _names(), (
        f"re-enabled tool has no row and nothing restores it: {sorted(_names())}"
    )
    resolved = await fresh._try_lazy_load(
        ToolCall(id="1", name="files_list", arguments={})
    )
    assert resolved is not None, "re-enabled tool stayed unresolvable"
    assert not resolved.is_error, resolved.content


@pytest.mark.asyncio
async def test_reload_and_session_build_produce_the_same_rows(session_env):
    """The two writers of the index must agree on the row set."""
    loop = await _build("s-29g")
    from_build = _names()
    assert from_build == {"files_list", "user_only"}, (
        f"fixture no longer produces the expected rows: {sorted(from_build)}"
    )

    assert not _reload(loop).startswith("Error"), "reload failed"
    from_reload = _names()

    assert from_build == from_reload, (
        f"runner and tool_reload disagree: only in build {from_build - from_reload}, "
        f"only in reload {from_reload - from_build}"
    )
