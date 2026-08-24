"""Tests for crash-safe tool-index hash commit ordering (audit P6).

The contract: get_or_create_index() must NOT persist source hashes until the
caller finishes indexing. A crash mid-indexing must leave stale hashes in
place so the next start recomputes needs_reindex and self-heals.
"""

from pathlib import Path

from src.sdk.tool_index import ToolIndex, get_or_create_index
from src.sdk.tools import ToolDefinition


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    tools_dir = tmp_path / "Tools"
    tools_dir.mkdir()
    mcp_config = tmp_path / ".mcp.json"
    mcp_config.write_text("{}")
    index_dir = tmp_path / ".tool_index"
    return tools_dir, mcp_config, index_dir


def test_hashes_not_saved_until_caller_commits(tmp_path):
    tools_dir, mcp_config, index_dir = _setup(tmp_path)
    hashes_path = index_dir / ".index_hashes.json"

    idx, commit_hashes = get_or_create_index(
        tools_dir, None, mcp_config, index_dir=index_dir
    )
    # Hashes must NOT be persisted before the caller finishes indexing.
    assert not hashes_path.exists()

    idx.index_tool(
        ToolDefinition(name="demo", description="Demo tool"), tool_type="custom"
    )
    # Still not saved: caller has not committed yet.
    assert not hashes_path.exists()

    commit_hashes()
    assert hashes_path.exists()


def test_crash_before_commit_reindexes_next_start(tmp_path):
    tools_dir, mcp_config, index_dir = _setup(tmp_path)

    idx, commit_hashes = get_or_create_index(
        tools_dir, None, mcp_config, index_dir=index_dir
    )
    idx.index_tool(
        ToolDefinition(name="demo", description="Demo tool"), tool_type="custom"
    )
    # Crash: commit_hashes() is never called.
    idx.close()

    # Next start: hashes still missing/old -> needs_reindex -> cleared.
    idx2, _ = get_or_create_index(
        tools_dir, None, mcp_config, index_dir=index_dir
    )
    assert idx2.count() == 0


def test_no_reindex_when_hashes_committed_and_unchanged(tmp_path):
    tools_dir, mcp_config, index_dir = _setup(tmp_path)

    idx, commit_hashes = get_or_create_index(
        tools_dir, None, mcp_config, index_dir=index_dir
    )
    idx.index_tool(
        ToolDefinition(name="demo", description="Demo tool"), tool_type="custom"
    )
    commit_hashes()
    idx.close()

    idx2, _ = get_or_create_index(
        tools_dir, None, mcp_config, index_dir=index_dir
    )
    # Sources unchanged and hashes committed -> index preserved.
    assert idx2.count() == 1


def test_clear_uses_table_level_delete(tmp_path):
    idx = ToolIndex(tmp_path / "index")
    idx.index_tool(
        ToolDefinition(name="temp", description="Temp"), tool_type="custom"
    )
    assert idx.count() == 1

    idx.clear()
    assert idx.count() == 0
    assert idx.list_all_names() == []

    idx.close()


def test_index_tools_bulk_upsert_no_duplicates(tmp_path):
    """index_tools with repeated names must upsert, not duplicate rows."""
    idx = ToolIndex(tmp_path / "index")
    tools = [
        ToolDefinition(name="tool_a", description="First"),
        ToolDefinition(name="tool_b", description="Second"),
    ]
    idx.index_tools(tools, tool_type="custom")
    # Re-index the same tools (fresh sources) -> names still unique.
    idx.index_tools(tools, tool_type="custom")

    assert idx.count() == 2
    assert sorted(idx.list_all_names()) == ["tool_a", "tool_b"]

    idx.close()
