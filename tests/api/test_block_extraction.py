"""Tests for block extraction (diff, suggestion, canvas, editor, directive)."""

from src.http.routers.conversation import (
    _extract_coedit_surfaces,
    _extract_diff,
    _extract_suggestions,
    _extract_surfaces,
    _strip_canvas_fences,
)
from src.http.routers.ws import parse_directives


class TestExtractDiff:
    """Tests for _extract_diff diff:path fence extraction."""

    def test_single_diff(self):
        text = 'Here is the change:\n```diff:src/main.py\n-old line\n+new line\n```'
        result = _extract_diff(text)
        assert len(result) == 1
        assert result[0]["surface_type"] == "diff_card"
        assert result[0]["file_path"] == "src/main.py"
        assert result[0]["diff"] == "-old line\n+new line"

    def test_multiple_diffs(self):
        text = (
            '```diff:src/a.py\n-a\n+b\n```\n'
            'some text\n'
            '```diff:src/b.py\n-c\n+d\n```'
        )
        result = _extract_diff(text)
        assert len(result) == 2
        assert result[0]["file_path"] == "src/a.py"
        assert result[1]["file_path"] == "src/b.py"

    def test_empty_diff(self):
        text = '```diff:src/main.py\n\n```'
        result = _extract_diff(text)
        assert result == []

    def test_no_diff(self):
        text = "Just some regular text with no fences."
        result = _extract_diff(text)
        assert result == []

    def test_malformed_no_newline_before_close(self):
        text = '```diff:src/main.py\nsome change```'
        result = _extract_diff(text)
        assert len(result) == 1
        assert result[0]["diff"] == "some change"

    def test_diff_with_extra_whitespace(self):
        text = '```diff:  src/main.py   \n\n  -old\n  +new\n```'
        result = _extract_diff(text)
        assert len(result) == 1
        assert result[0]["file_path"] == "src/main.py"

    def test_diff_empty_path_skipped(self):
        text = '```diff:  \n-old\n+new\n```'
        result = _extract_diff(text)
        assert result == []

    def test_diff_none_input(self):
        result = _extract_diff("")
        assert result == []


class TestExtractSuggestions:
    """Tests for _extract_suggestions suggestion fence extraction."""

    def test_single_suggestion(self):
        text = '```suggestion\nfilePath:src/main.py\n---\nnew file content\n```'
        result = _extract_suggestions(text)
        assert len(result) == 1
        assert result[0]["surface_type"] == "suggestion"
        assert result[0]["file_path"] == "src/main.py"
        assert result[0]["content"] == "new file content"

    def test_multiple_suggestions(self):
        text = (
            '```suggestion\nfilePath:a.py\n---\ncontent a\n```\n'
            '```suggestion\nfilePath:b.py\n---\ncontent b\n```'
        )
        result = _extract_suggestions(text)
        assert len(result) == 2
        assert result[0]["file_path"] == "a.py"
        assert result[1]["file_path"] == "b.py"

    def test_empty_suggestion(self):
        text = '```suggestion\nfilePath:main.py\n---\n\n```'
        result = _extract_suggestions(text)
        assert result == []

    def test_no_suggestion(self):
        text = "Just some regular text."
        result = _extract_suggestions(text)
        assert result == []

    def test_suggestion_with_whitespace_path(self):
        text = '```suggestion\nfilePath:  src/main.py  \n---\ncontent\n```'
        result = _extract_suggestions(text)
        assert len(result) == 1
        assert result[0]["file_path"] == "src/main.py"

    def test_suggestion_empty_path_skipped(self):
        text = '```suggestion\nfilePath:  \n---\ncontent\n```'
        result = _extract_suggestions(text)
        assert result == []

    def test_suggestion_none_input(self):
        result = _extract_suggestions("")
        assert result == []


class TestExtractCoeditSurfaces:
    """Tests for _extract_coedit_surfaces returning (diffs, suggestions)."""

    def test_returns_tuple(self):
        text = (
            '```diff:src/a.py\n-old\n+new\n```\n'
            '```suggestion\nfilePath:b.py\n---\ncontent\n```'
        )
        diffs, suggestions = _extract_coedit_surfaces(text)
        assert len(diffs) == 1
        assert diffs[0]["file_path"] == "src/a.py"
        assert len(suggestions) == 1
        assert suggestions[0]["file_path"] == "b.py"

    def test_empty_text(self):
        diffs, suggestions = _extract_coedit_surfaces("")
        assert diffs == []
        assert suggestions == []

    def test_only_diffs(self):
        text = '```diff:src/main.py\n-old\n+new\n```'
        diffs, suggestions = _extract_coedit_surfaces(text)
        assert len(diffs) == 1
        assert suggestions == []

    def test_only_suggestions(self):
        text = '```suggestion\nfilePath:a.py\n---\ncontent\n```'
        diffs, suggestions = _extract_coedit_surfaces(text)
        assert diffs == []
        assert len(suggestions) == 1


class TestStripCanvasFences:
    """Tests for _strip_canvas_fences removing all fence types."""

    def test_strips_canvas_fence(self):
        text = 'Before ```html:canvas\n<div>hi</div>\n``` After'
        result = _strip_canvas_fences(text)
        assert "```html:canvas" not in result
        assert "Before" in result
        assert "After" in result

    def test_strips_editor_fence(self):
        text = 'Before ```html:editor\nfilePath:x.py\n---\ncontent\n``` After'
        result = _strip_canvas_fences(text)
        assert "```html:editor" not in result
        assert "Before" in result
        assert "After" in result

    def test_strips_diff_fence(self):
        text = 'Before ```diff:src/main.py\n-old\n+new\n``` After'
        result = _strip_canvas_fences(text)
        assert "```diff:src/main.py" not in result
        assert "Before" in result
        assert "After" in result

    def test_strips_suggestion_fence(self):
        text = 'Before ```suggestion\nfilePath:a.py\n---\ncontent\n``` After'
        result = _strip_canvas_fences(text)
        assert "```suggestion" not in result
        assert "Before" in result
        assert "After" in result

    def test_strips_all_fence_types(self):
        text = (
            '```html:canvas\n<div>x</div>\n```\n'
            '```html:editor\nfilePath:x.py\n---\neditor\n```\n'
            '```diff:src/a.py\n-old\n+new\n```\n'
            '```suggestion\nfilePath:b.py\n---\nsug\n```\n'
            'remaining'
        )
        result = _strip_canvas_fences(text)
        assert "```" not in result
        assert result == "remaining"

    def test_no_fences(self):
        text = "Plain text with no fences."
        result = _strip_canvas_fences(text)
        assert result == text

    def test_strips_uppercase_fence(self):
        text = 'Before ```DIFF:src/main.py\n-old\n+new\n``` After'
        result = _strip_canvas_fences(text)
        assert "```" not in result
        assert result == "Before  After"


class TestExtractSurfaces:
    """Tests for _extract_surfaces combining canvas, editor, etc."""

    def test_canvas_and_editor_together(self):
        text = (
            '```html:canvas\n<div>canvas</div>\n```\n'
            '```html:editor\nfilePath:x.py\n---\ncontent\n```'
        )
        surfaces = _extract_surfaces(text)
        assert len(surfaces) == 2
        assert surfaces[0]["surface_type"] == "canvas"
        assert surfaces[1]["surface_type"] == "editor"

    def test_no_surfaces(self):
        text = "No surface blocks here."
        surfaces = _extract_surfaces(text)
        assert surfaces == []


class TestParseDirectivesOpenEditor:
    """Tests for [OPEN_EDITOR] directive parsing."""

    def test_open_editor_basic(self):
        text, events = parse_directives("[OPEN_EDITOR:src/main.py]")
        assert len(events) == 1
        assert events[0]["type"] == "open_editor"
        assert events[0]["file_path"] == "src/main.py"
        assert events[0]["tab"] == "notes"

    def test_open_editor_stripped_from_text(self):
        text, events = parse_directives("Check this file: [OPEN_EDITOR:src/main.py]")
        assert "[OPEN_EDITOR:" not in text
        assert "Check this file:" in text

    def test_open_editor_multiple(self):
        text, events = parse_directives(
            "[OPEN_EDITOR:a.py] and [OPEN_EDITOR:b.py]"
        )
        assert len(events) == 2
        assert events[0]["file_path"] == "a.py"
        assert events[1]["file_path"] == "b.py"

    def test_open_editor_with_path_spaces(self):
        text, events = parse_directives("[OPEN_EDITOR:src/main.py]")
        assert events[0]["file_path"] == "src/main.py"

    def test_open_editor_mixed_with_other_directives(self):
        text, events = parse_directives(
            "[SWITCH_TAB:email][OPEN_EDITOR:main.py]Check your email"
        )
        types = [e["type"] for e in events]
        assert "open_editor" in types
        assert "tab_update" in types

    def test_open_editor_empty_path_no_match(self):
        text, events = parse_directives("[OPEN_EDITOR:]")
        assert events == []  # regex requires non-empty path

    def test_no_directives(self):
        text, events = parse_directives("Just a regular response.")
        assert events == []
        assert text == "Just a regular response."

    def test_empty_text_no_directives(self):
        text, events = parse_directives("")
        assert events == []
        assert text == ""
