"""Custom TOOL.md pipeline semantics."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.sdk.tools import ToolDefinition, ToolResult
from src.sdk.tools_custom import _parse_tool_file


@pytest.fixture(autouse=True)
def soft_backend(monkeypatch):
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


def make_tool(tmp_path: Path, command: str, annotations: dict | None = None) -> ToolDefinition:
    lines = [
        "---",
        "name: pipeline_fixture",
        "description: Pipeline fixture",
        f"command: {command}",
    ]
    if annotations:
        lines.append("annotations:")
        lines.extend(f"  {key}: {value}" for key, value in annotations.items())
    lines.append("---")
    tool_file = tmp_path / "TOOL.md"
    tool_file.write_text("\n".join(lines) + "\n")
    tool = _parse_tool_file(tool_file)
    assert tool is not None
    return tool


def test_pipefail_opt_in_reports_early_pipeline_failure(tmp_path, scoped_paths):
    tool = make_tool(tmp_path, "false | cat", annotations={"pipefail": True})

    result = tool.function()

    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["outcome"] == "failed"


def test_default_pipeline_semantics_remain_compatible(tmp_path, scoped_paths):
    tool = make_tool(tmp_path, "false | cat")

    assert tool.function() == "(no output)"


def test_pipefail_annotation_is_parsed_from_tool_md(tmp_path, scoped_paths):
    tool = make_tool(tmp_path, "true | cat", annotations={"pipefail": True})

    assert tool.annotations.pipefail is True
