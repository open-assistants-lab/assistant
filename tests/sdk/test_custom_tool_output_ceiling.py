"""Custom command output above the sandbox capture ceiling is explicit."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.sdk.tools import ToolDefinition, ToolResult
from src.sdk.tools_custom import _parse_tool_file


@pytest.fixture(autouse=True)
def limited_soft_backend(monkeypatch):
    from src.config import reload_settings

    monkeypatch.setenv("SANDBOX_BACKEND", "soft")
    reload_settings()
    yield
    reload_settings()


@pytest.fixture
def scoped_paths(tmp_path, monkeypatch):
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


def make_large_output_tool(tmp_path: Path, size: int = 1_000_000) -> ToolDefinition:
    script = tmp_path / "emit_large.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write('x' * {size})\n",
        encoding="utf-8",
    )
    tool_file = tmp_path / "TOOL.md"
    tool_file.write_text(
        "---\n"
        "name: large_output\n"
        "description: Large output fixture\n"
        f"command: '{sys.executable} {script}'\n"
        "---\n",
        encoding="utf-8",
    )
    tool = _parse_tool_file(tool_file)
    assert tool is not None
    return tool


def test_custom_command_above_capture_ceiling_is_explicitly_truncated(tmp_path, scoped_paths):
    tool = make_large_output_tool(tmp_path)

    result = tool.function()

    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["truncated"] is True
    assert result.structured_content["capture_limit_bytes"] == 800 * 1024
    assert "tool_result_read" not in result.content
