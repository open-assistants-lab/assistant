"""The bounded test runner: an affordance, not a shell escape."""

from __future__ import annotations

from pathlib import Path

import pytest

# The fixture project must have a failing test to make the tool's honest output observable.
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "bench_project"


def _tool():
    from src.sdk.tools_core.run_tests import run_tests

    return run_tests


@pytest.fixture(autouse=True)
def _allowed_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("FILESYSTEM_ALLOWED_ROOTS", str(tmp_path))
    from src.config import reload_settings

    reload_settings()
    yield
    reload_settings()


def test_it_runs_a_suite_and_reports_pass(tmp_path: Path) -> None:
    d = tmp_path / "proj"
    d.mkdir()
    (d / "test_ok.py").write_text("def test_a():\n    assert 1 == 1\n")
    import asyncio

    from src.sdk.tools_core.run_tests import run_tests

    res = asyncio.run(run_tests.ainvoke({"directory": str(d), "user_id": "u"}))
    assert "RESULT: ALL PASS" in res.content
    assert res.is_error is False


def test_it_reports_failure_honestly_and_points_at_the_test(tmp_path: Path) -> None:
    import asyncio

    from src.sdk.tools_core.run_tests import run_tests

    d = tmp_path / "bad"
    d.mkdir()
    (d / "test_bad.py").write_text("def test_b():\n    assert 1 == 2\n")
    res = asyncio.run(run_tests.ainvoke({"directory": str(d), "user_id": "u"}))
    assert "RESULT: FAILING" in res.content
    assert res.is_error is True
    assert "read it before editing" in res.content


def test_it_refuses_a_directory_outside_allowed_roots(tmp_path: Path) -> None:
    import asyncio

    from src.sdk.tools_core.run_tests import run_tests

    outside = tmp_path.parent / "elsewhere-test-run-tests"
    outside.mkdir(exist_ok=True)
    res = asyncio.run(run_tests.ainvoke({"directory": str(outside), "user_id": "u"}))
    assert res.is_error is True
    assert "allowed root" in res.content


def test_it_refuses_a_missing_directory(tmp_path: Path) -> None:
    import asyncio

    from src.sdk.tools_core.run_tests import run_tests

    res = asyncio.run(
        run_tests.ainvoke({"directory": str(tmp_path / "nope" / "nope"), "user_id": "u"})
    )
    assert res.is_error is True
    assert "does not exist" in res.content


def test_it_is_not_auto_approved() -> None:
    """A test runner executes arbitrary code; read_only would be a hole."""
    t = _tool()
    assert t.annotations.read_only is not True
    assert t.annotations.destructive is not True


def test_it_is_registered() -> None:
    from src.sdk.native_tools import get_native_tools

    assert "run_tests" in {t.name for t in get_native_tools()}
