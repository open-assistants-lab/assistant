"""Tests for the unified diff utility."""

from src.http.diff_utils import apply_unified_diff


class TestApplyUnifiedDiff:
    """Tests for apply_unified_diff with context validation."""

    def test_simple_addition(self):
        original = "line1\nline2\nline3"
        diff = [
            " line1",
            " line2",
            "+new_line",
            " line3",
        ]
        result = apply_unified_diff(original, diff)
        assert result == "line1\nline2\nnew_line\nline3"

    def test_simple_deletion(self):
        original = "line1\nline2\nline3"
        diff = [
            " line1",
            "-line2",
            " line3",
        ]
        result = apply_unified_diff(original, diff)
        assert result == "line1\nline3"

    def test_replacement(self):
        original = "line1\nold_line\nline3"
        diff = [
            " line1",
            "-old_line",
            "+new_line",
            " line3",
        ]
        result = apply_unified_diff(original, diff)
        assert result == "line1\nnew_line\nline3"

    def test_multiple_hunks(self):
        original = "a\nb\nc\nd\ne"
        diff = [
            "-a",
            "+A",
            " b",
            " c",
            "",
            " d",
            "-e",
            "+E",
        ]
        result = apply_unified_diff(original, diff)
        assert result == "A\nb\nc\nd\nE"

    def test_context_mismatch_deleted_line(self):
        original = "line1\nWRONG\nline3"
        diff = [
            " line1",
            "-line2",
            " line3",
        ]
        result = apply_unified_diff(original, diff)
        assert result is None

    def test_context_mismatch_context_line(self):
        original = "line1\nline2\nWRONG"
        diff = [
            " line1",
            " line2",
            " line3",
        ]
        result = apply_unified_diff(original, diff)
        assert result is None

    def test_empty_diff(self):
        original = "line1\nline2"
        result = apply_unified_diff(original, [])
        assert result == "line1\nline2"

    def test_blank_lines_in_diff_are_skipped(self):
        original = "a\nb"
        diff = [
            "",
            "",
            " a",
            "",
            " b",
        ]
        result = apply_unified_diff(original, diff)
        assert result == "a\nb"

    def test_empty_context_line(self):
        original = "line1\n\nline3"
        diff = [
            " line1",
            " ",
            " line3",
        ]
        result = apply_unified_diff(original, diff)
        assert result == "line1\n\nline3"

    def test_malformed_diff_line(self):
        original = "hello"
        diff = ["|garbage"]
        result = apply_unified_diff(original, diff)
        assert result is None

    def test_append_to_end(self):
        original = "line1\nline2"
        diff = [
            " line1",
            " line2",
            "+line3",
        ]
        result = apply_unified_diff(original, diff)
        assert result == "line1\nline2\nline3"

    def test_prepend_at_start(self):
        original = "line2\nline3"
        diff = [
            "+line1",
            " line2",
            " line3",
        ]
        result = apply_unified_diff(original, diff)
        assert result == "line1\nline2\nline3"

    def test_context_mismatch_out_of_bounds(self):
        original = "only_one_line"
        diff = [
            " line1",
            " line2",
        ]
        result = apply_unified_diff(original, diff)
        assert result is None

    def test_no_changes_context_only(self):
        original = "a\nb\nc"
        diff = [
            " a",
            " b",
            " c",
        ]
        result = apply_unified_diff(original, diff)
        assert result == "a\nb\nc"

    def test_empty_original_with_additions(self):
        original = ""
        diff = [
            "+hello",
            "+world",
        ]
        result = apply_unified_diff(original, diff)
        assert result == "hello\nworld"

    def test_no_newline_at_end_of_file_handling(self):
        original = "line1\nline2"
        diff = [
            " line1",
            " line2",
            "+line3",
        ]
        result = apply_unified_diff(original, diff)
        assert result == "line1\nline2\nline3"
