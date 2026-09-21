"""Unit tests for the mypy ratchet (scripts/mypy_baseline.py, src/quality_gate.py)."""

from __future__ import annotations

import pytest

from src.quality_gate import compare, load_baseline, parse_errors, render_baseline

MYPY_OUTPUT = """\
src/http/routers/tenancy.py:71: error: Incompatible return value type  [return-value]
src/http/routers/tenancy.py:92: error: Incompatible return value type  [return-value]
src/sdk/run_service.py:572: error: Incompatible return value type  [return-value]
src/sdk/run_service.py:572: note: Use https://example.invalid to upgrade
Found 3 errors in 2 files (checked 192 source files)
"""


class TestParseErrors:
    def test_counts_errors_per_file(self):
        counts = parse_errors(MYPY_OUTPUT)
        assert counts == {
            "src/http/routers/tenancy.py": 2,
            "src/sdk/run_service.py": 1,
        }

    def test_notes_and_summary_are_not_errors(self):
        counts = parse_errors(
            "src/a.py:1: note: something\nFound 0 errors in 0 files (checked 1 source file)\n"
        )
        assert counts == {}


class TestCompare:
    def test_growth_is_a_regression(self):
        problems = compare({"src/a.py": 3}, {"src/a.py": 2})
        assert problems and "src/a.py" in problems[0]

    def test_new_file_is_a_regression(self):
        problems = compare({"src/b.py": 1}, {})
        assert problems and "src/b.py" in problems[0]

    def test_reduction_is_allowed(self):
        assert compare({"src/a.py": 1}, {"src/a.py": 2}) == []


class TestBaselineFile:
    def test_render_then_load_round_trips(self, tmp_path):
        path = tmp_path / "mypy-baseline.txt"
        path.write_text(render_baseline({"src/a.py": 2, "src/b.py": 1}))
        assert load_baseline(path) == {"src/a.py": 2, "src/b.py": 1}

    def test_missing_baseline_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_baseline(tmp_path / "missing.txt")

    def test_comments_and_blank_lines_are_ignored(self, tmp_path):
        path = tmp_path / "baseline.txt"
        path.write_text("# comment\n\nsrc/a.py: 4\n")
        assert load_baseline(path) == {"src/a.py": 4}
