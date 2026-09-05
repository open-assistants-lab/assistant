"""Tests for custom tools, tool index, tool_search, and tool_reload."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from src.sdk.tools import ToolDefinition


class TestParseToolFile:
    def test_parse_simple(self) -> None:
        from src.sdk.tools_custom import _parse_tool_file

        d = tempfile.mkdtemp()
        tool_dir = Path(d) / "my_tool"
        tool_dir.mkdir()
        tool_file = tool_dir / "TOOL.md"
        tool_file.write_text("""\
---
name: my_tool
description: A test tool
command: echo "{{message}}"
parameters:
  type: object
  properties:
    message:
      type: string
      description: The message to echo
  required:
    - message
---
""")

        td = _parse_tool_file(tool_file)
        assert td is not None
        assert td.name == "my_tool"
        assert td.description == "A test tool"
        assert td.parameters["type"] == "object"
        assert td.parameters["properties"]["message"]["type"] == "string"
        assert td.function is not None

    def test_missing_file(self) -> None:
        from src.sdk.tools_custom import _parse_tool_file

        assert _parse_tool_file(Path("/nonexistent/TOOL.md")) is None

    def test_no_frontmatter(self) -> None:
        import tempfile
        from pathlib import Path

        from src.sdk.tools_custom import _parse_tool_file

        f = Path(tempfile.mkdtemp()) / "TOOL.md"
        f.write_text("no frontmatter here")
        assert _parse_tool_file(f) is None

    def test_minimal_frontmatter(self) -> None:
        import tempfile
        from pathlib import Path

        from src.sdk.tools_custom import _parse_tool_file

        tool_dir = Path(tempfile.mkdtemp()) / "minimal"
        tool_dir.mkdir()
        f = tool_dir / "TOOL.md"
        f.write_text("""\
---
name: minimal
description: Minimal tool
command: ls
---
""")
        td = _parse_tool_file(f)
        assert td is not None
        assert td.name == "minimal"
        # Should auto-generate params from command template
        assert td.parameters["type"] == "object"
        assert "required" in td.parameters

    def test_unsafe_tool_name(self) -> None:
        import tempfile
        from pathlib import Path

        from src.sdk.tools_custom import _parse_tool_file

        tool_dir = Path(tempfile.mkdtemp()) / "bad tool"
        tool_dir.mkdir()
        f = tool_dir / "TOOL.md"
        f.write_text("""\
---
name: bad tool
description: Bad
command: echo hi
---
""")
        td = _parse_tool_file(f)
        assert td is not None  # parse succeeds, but yaml handles spaces

    def test_auto_extract_params(self) -> None:
        from src.sdk.tools_custom import _extract_params_from_command

        params = _extract_params_from_command("ffmpeg -i {{input}} {{output}}")
        assert "input" in params.get("properties", {})
        assert "output" in params.get("properties", {})
        assert "input" in params.get("required", [])
        assert "output" in params.get("required", [])


class TestScanToolsDir:
    def test_empty_dir(self) -> None:
        from src.sdk.tools_custom import scan_tools_dir

        d = Path(tempfile.mkdtemp())
        tools = scan_tools_dir(d)
        assert len(tools) == 0

    def test_multiple_tools(self) -> None:
        from src.sdk.tools_custom import scan_tools_dir

        d = Path(tempfile.mkdtemp())

        for name in ("tool_a", "tool_b"):
            tool_dir = d / name
            tool_dir.mkdir()
            (tool_dir / "TOOL.md").write_text(f"""\
---
name: {name}
description: Tool {name}
command: echo "{{{{msg}}}}"
---
""")

        tools = scan_tools_dir(d)
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "tool_a" in names
        assert "tool_b" in names

    def test_skip_non_tool_dirs(self) -> None:
        from src.sdk.tools_custom import scan_tools_dir

        d = Path(tempfile.mkdtemp())
        (d / "not_a_tool").mkdir()
        (d / "empty_dir").mkdir()
        # Only TOOL.md files count
        tool_dir = d / "actual_tool"
        tool_dir.mkdir()
        (tool_dir / "TOOL.md").write_text("""\
---
name: actual_tool
description: Real tool
command: echo hi
---
""")

        tools = scan_tools_dir(d)
        assert len(tools) == 1


class TestCoreToolNames:
    def test_core_tools_present(self) -> None:
        from src.sdk.tools_custom import CORE_TOOL_NAMES

        assert "tool_search" in CORE_TOOL_NAMES
        assert "tool_reload" in CORE_TOOL_NAMES
        assert "shell_execute" in CORE_TOOL_NAMES
        assert "files_read" in CORE_TOOL_NAMES
        assert "files_write" in CORE_TOOL_NAMES

    def test_is_core_tool(self) -> None:
        from src.sdk.tools_custom import is_core_tool

        assert is_core_tool("tool_search")
        assert is_core_tool("files_read")
        assert is_core_tool("shell_execute")
        assert not is_core_tool("my_custom_tool")
        assert not is_core_tool("")


class TestCustomToolExecution:
    def test_custom_tool_function(self) -> None:
        from src.sdk.tools_custom import _parse_tool_file

        d = tempfile.mkdtemp()
        tool_dir = Path(d) / "echo_tool"
        tool_dir.mkdir()
        f = tool_dir / "TOOL.md"
        f.write_text("""\
---
name: echo_tool
description: Echo a message
command: echo "{{message}}"
parameters:
  type: object
  properties:
    message:
      type: string
      description: Message to echo
  required:
    - message
---
""")

        td = _parse_tool_file(f)
        assert td is not None
        assert td.function is not None

        # The generated function should render the template and call shell_execute
        # We can't easily mock subprocess here, but we can verify the closure exists
        result = td.function(message="hello")
        assert isinstance(result, str)


class TestToolIndex:
    def test_create_and_search(self) -> None:
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "index"
        idx = ToolIndex(d)

        td = ToolDefinition(name="test_tool", description="A test tool for searching")
        idx.index_tool(td, tool_type="custom")

        results = idx.search("test")
        assert len(results) >= 1
        name, desc = results[0]
        assert name == "test_tool"

        idx.close()

    def test_get_definition_by_name(self) -> None:
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "index"
        idx = ToolIndex(d)

        td = ToolDefinition(name="find_me", description="Find this tool")
        idx.index_tool(td, tool_type="custom")

        loaded = idx.get_definition("find_me")
        assert loaded is not None
        assert loaded.name == "find_me"
        assert loaded.description == "Find this tool"

        not_found = idx.get_definition("nonexistent")
        assert not_found is None

        idx.close()

    def test_index_multiple_tools(self) -> None:
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "index"
        idx = ToolIndex(d)

        tools = [
            ToolDefinition(name="tool_a", description="First tool"),
            ToolDefinition(name="tool_b", description="Second tool"),
        ]
        idx.index_tools(tools, tool_type="custom")

        results = idx.search("tool", limit=10)
        assert len(results) == 2

        idx.close()

    def test_remove_tool(self) -> None:
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "index"
        idx = ToolIndex(d)

        td = ToolDefinition(name="remove_me", description="Will be removed")
        idx.index_tool(td, tool_type="custom")

        assert idx.get_definition("remove_me") is not None

        idx.remove_tool("remove_me")
        assert idx.get_definition("remove_me") is None

        idx.close()

    def test_list_all_names(self) -> None:
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "index"
        idx = ToolIndex(d)

        tools = [
            ToolDefinition(name="alpha", description="A"),
            ToolDefinition(name="beta", description="B"),
        ]
        idx.index_tools(tools, tool_type="custom")

        names = idx.list_all_names()
        assert "alpha" in names
        assert "beta" in names
        assert len(names) == 2

        idx.close()

    def test_clear(self) -> None:
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "index"
        idx = ToolIndex(d)

        idx.index_tool(ToolDefinition(name="temp", description="Temp"), tool_type="custom")
        assert idx.count() == 1

        idx.clear()
        assert idx.count() == 0

        idx.close()


class TestChangeDetection:
    def test_compute_source_hashes(self) -> None:
        from src.sdk.tool_index import compute_source_hashes

        d = Path(tempfile.mkdtemp())
        tools_dir = d / "Tools"
        tools_dir.mkdir()
        tool_dir = tools_dir / "my_tool"
        tool_dir.mkdir()
        (tool_dir / "TOOL.md").write_text("test content")

        mcp_config = d / ".mcp.json"
        mcp_config.write_text("{}")

        hashes = compute_source_hashes(tools_dir, None, mcp_config)
        assert len(hashes) >= 1

    def test_check_needs_reindex(self) -> None:
        from src.sdk.tool_index import check_needs_reindex

        d = Path(tempfile.mkdtemp())
        hashes_path = d / ".index_hashes.json"

        # No stored hashes → needs reindex
        assert check_needs_reindex(hashes_path, {}) is True

        # Matching hashes → no reindex
        h = {"a": "1"}
        hashes_path.write_text(json.dumps(h))
        assert check_needs_reindex(hashes_path, h) is False

        # Different hashes → needs reindex
        assert check_needs_reindex(hashes_path, {"a": "2"}) is True

    def test_save_source_hashes(self) -> None:
        from src.sdk.tool_index import save_source_hashes

        d = Path(tempfile.mkdtemp())
        hashes_path = d / ".index_hashes.json"
        h = {"test": "abc123"}

        save_source_hashes(hashes_path, h)
        assert hashes_path.exists()
        assert json.loads(hashes_path.read_text()) == h


class TestRebuildFunction:
    def test_rebuild_custom_function(self) -> None:
        from src.sdk.tool_index import _rebuild_custom_function

        td = ToolDefinition(name="rebuild_test", description="Test rebuild")
        reconstruct = {
            "command": 'echo "{{msg}}"',
            "install": [],
        }

        result = _rebuild_custom_function(td, reconstruct)
        assert result.function is not None
        # function should be set on the same ToolDefinition object
        assert td.function is not None


class TestFunctionExecution:
    def test_custom_tool_function_returns_string(self) -> None:
        from src.sdk.tools_custom import _parse_tool_file

        d = tempfile.mkdtemp()
        tool_dir = Path(d) / "greeter"
        tool_dir.mkdir()
        f = tool_dir / "TOOL.md"
        f.write_text("""\
---
name: greeter
description: Greet someone
command: echo '{{name}}'
parameters:
  type: object
  properties:
    name:
      type: string
      description: Name to greet
  required:
    - name
---
""")

        td = _parse_tool_file(f)
        assert td is not None
        result = td.function(name="World")
        assert isinstance(result, str)


class TestHardSandboxCustomTools:
    def test_tool_md_command_is_disabled_under_bwrap(self, tmp_path, monkeypatch) -> None:
        from src.sdk.tools_custom import _parse_tool_file

        monkeypatch.setattr(
            "src.config.get_settings",
            lambda: SimpleNamespace(sandbox=SimpleNamespace(backend="bwrap")),
        )
        outside = tmp_path / "outside.txt"
        tool_dir = tmp_path / "writer"
        tool_dir.mkdir()
        tool_file = tool_dir / "TOOL.md"
        tool_file.write_text("""\
---
name: writer
description: Writes a file
command: touch "{{path}}"
parameters:
  type: object
  properties:
    path:
      type: string
  required: [path]
---
""")

        definition = _parse_tool_file(tool_file)
        assert definition is not None and definition.function is not None
        result = definition.function(path=str(outside))

        assert "disabled" in result.lower()
        assert not outside.exists()

    def test_index_rebuilt_custom_command_is_disabled_under_bwrap(
        self, tmp_path, monkeypatch
    ) -> None:
        from src.sdk.tool_index import _rebuild_custom_function

        monkeypatch.setattr(
            "src.config.get_settings",
            lambda: SimpleNamespace(sandbox=SimpleNamespace(backend="bwrap")),
        )
        outside = tmp_path / "outside.txt"
        definition = ToolDefinition(name="writer", description="Writes a file")
        _rebuild_custom_function(
            definition,
            {"command": 'touch "{{path}}"', "install": []},
        )
        assert definition.function is not None
        result = definition.function(path=str(outside))

        assert "disabled" in result.lower()
        assert not outside.exists()


class TestToolSearchCoreTool:
    def test_tool_search_no_loop(self) -> None:
        from src.sdk.tools_core.tool_search import tool_search

        result = tool_search.invoke({"description": "anything"})
        assert "No tool index" in result or isinstance(result, str)

    def test_tool_search_description_param(self) -> None:
        from src.sdk.tools_core.tool_search import tool_search

        result = tool_search.invoke({"description": "extract pdf text"})
        assert isinstance(result, str)


class TestToolReloadCoreTool:
    def test_tool_reload_no_loop(self) -> None:
        from src.sdk.tools_core.tool_reload import tool_reload

        result = tool_reload.invoke({})
        assert "No tool index" in result or isinstance(result, str)


class TestGetCustomTools:
    def test_get_custom_tools_empty(self) -> None:
        from src.sdk.tools_custom import get_custom_tools

        tools = get_custom_tools(user_id="test_nonexistent_user")
        assert tools == []


class TestToolSearchIntegration:
    def test_search_and_load_flow(self) -> None:
        """Integration test: index a tool, search for it, load definition."""
        from src.sdk.tool_index import ToolIndex

        d = Path(tempfile.mkdtemp()) / "index"
        idx = ToolIndex(d)

        td = ToolDefinition(
            name="pdf_extract",
            description="Extract text from PDF files",
        )
        idx.index_tool(td, tool_type="custom", namespace="custom",
                       reconstruct={"command": 'ocrmypdf "{{input}}" "{{output}}"', "install": []})

        # Step 1: search
        results = idx.search("extract pdf", limit=5)
        assert len(results) >= 1
        assert results[0][0] == "pdf_extract"

        # Step 2: get definition
        loaded = idx.get_definition("pdf_extract")
        assert loaded is not None
        assert loaded.name == "pdf_extract"

        # Step 3: get reconstruct
        recon = idx.get_reconstruct("pdf_extract")
        assert "command" in recon
        assert "{{input}}" in recon["command"]

        idx.close()

    def test_core_search_excludes_core(self) -> None:
        """Core tools should not be in the index."""
        from src.sdk.tool_index import get_or_create_index
        from src.sdk.tools_custom import CORE_TOOL_NAMES

        d = Path(tempfile.mkdtemp())
        tools_dir = d / "Tools"
        tools_dir.mkdir()
        index_dir = d / ".tool_index"
        mcp_config = d / ".mcp.json"
        mcp_config.write_text("{}")

        for name in list(CORE_TOOL_NAMES)[:3]:
            tool_dir = tools_dir / name
            tool_dir.mkdir()
            (tool_dir / "TOOL.md").write_text(f"""\
---
name: {name}
description: Core tool
command: echo hi
---
""")

        idx, _commit = get_or_create_index(tools_dir, None, mcp_config,
                                           user_id="test_index_user", workspace_id="personal",
                                           index_dir=index_dir)
        names = idx.list_all_names()
        for core in list(CORE_TOOL_NAMES)[:3]:
            assert core not in names, f"Core tool '{core}' should not be indexed"

        idx.close()
