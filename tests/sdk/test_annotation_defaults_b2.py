"""B2: annotation defaults must not treat unknown as safe (#108, #80)."""

from mcp.types import ToolAnnotations as McpAnnotations

from src.sdk.tools import ToolAnnotations
from src.sdk.tools_core.mcp_bridge import _convert_tool_annotations
from src.sdk.tools_custom import _parse_tool_file


class TestMcpAnnotationConversion:
    """#108: an unset destructiveHint made the tool look safe."""

    def test_unset_destructive_hint_on_a_non_read_only_tool_is_destructive(self):
        converted = _convert_tool_annotations(
            McpAnnotations(readOnlyHint=False, destructiveHint=None)
        )
        assert converted.destructive is True, (
            "a tool the server refused to describe as safe ran as parallel-safe"
        )

    def test_unset_hints_on_an_annotations_object_default_closed(self):
        converted = _convert_tool_annotations(McpAnnotations())
        assert converted.read_only is False
        assert converted.destructive is True

    def test_explicit_hints_are_honoured(self):
        converted = _convert_tool_annotations(
            McpAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
        )
        assert converted.read_only is True
        assert converted.destructive is False
        assert converted.idempotent is True

    def test_no_annotations_at_all_defaults_closed(self):
        converted = _convert_tool_annotations(None)
        assert isinstance(converted, ToolAnnotations)
        assert converted.read_only is False
        assert converted.destructive is True

    def test_read_only_tool_with_unset_destructive_is_not_destructive(self):
        """An explicitly read-only tool stays non-destructive."""
        converted = _convert_tool_annotations(
            McpAnnotations(readOnlyHint=True, destructiveHint=None)
        )
        assert converted.read_only is True
        assert converted.destructive is False


class TestCustomToolAnnotationDefaults:
    """#80: a TOOL.md tool without annotations was read_only=True — memoized
    by the duplicate guard, so a repeat call returned the FIRST result."""

    def test_default_is_not_read_only(self, tmp_path):
        tool_file = tmp_path / "TOOL.md"
        tool_file.write_text(
            "---\nname: probe\ndescription: d\ncommand: echo hi\n---\n",
            encoding="utf-8",
        )
        tool = _parse_tool_file(tool_file)
        assert tool is not None
        assert tool.annotations.read_only is False, (
            "an unannotated custom tool defaulted to memoized read"
        )
        assert tool.annotations.destructive is False

    def test_explicit_read_only_is_still_honoured(self, tmp_path):
        tool_file = tmp_path / "TOOL.md"
        tool_file.write_text(
            "---\nname: probe\ndescription: d\ncommand: echo hi\n"
            "annotations:\n  read_only: true\n---\n",
            encoding="utf-8",
        )
        tool = _parse_tool_file(tool_file)
        assert tool is not None
        assert tool.annotations.read_only is True
