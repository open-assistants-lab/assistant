"""A tool-body catch-all must report failure, not a plain string.

Three times a deliberate failure was swallowed by a tool's own
``except Exception: return f"...{e}"`` and reported as a successful run (#23
`shell_execute`, #24/#25 the custom `TOOL.md` wrappers). The mechanism is the
shape of the handler, so this walks the AST of every tool body and asserts the
convention directly.

``ALLOWED_SUCCESS_RETURNS`` lists handlers that legitimately return guidance
text from a broad handler because the tool completed its job. Keep it short:
each entry is a documented exception, not a parking space.

Reach: only `@tool`-decorated functions in this package are walked. Out of
scope, and reviewed by hand instead:

- a `@tool(name=...)` call form or a hand-built `ToolDefinition`
  (`mcp.py`, `research.py`, `mcp_bridge.py`);
- MCP and user-authored tool bodies;
- narrow handlers such as `except httpx.HTTPError`, which may legitimately
  return guidance text;
- a broad handler with no `return` of its own, which falls through to a later
  return (`files_write`'s version-capture failure is deliberate).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.sdk.execution_models import Outcome
from src.sdk.tools import ToolResult

TOOLS_CORE = pathlib.Path(__file__).resolve().parents[2] / "src" / "sdk" / "tools_core"

#: (file name, function name) -> why this broad handler returns text on success
ALLOWED_SUCCESS_RETURNS = {
    ("time.py", "time_get"): (
        "an unparseable timezone still returns a valid current time in UTC"
    ),
}


def _tool_functions(path: pathlib.Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(getattr(d, "id", None) == "tool" for d in node.decorator_list):
            yield node


def _broad_handlers(fn):
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            name = getattr(handler.type, "id", None) or getattr(handler.type, "attr", None)
            if name in {"Exception", "BaseException"} or handler.type is None:
                yield handler


def _is_failure_result(value) -> bool:
    """A ToolResult is only a failure if it says so.

    `ToolResult.is_error` defaults to False, so `return ToolResult(content=...)`
    would still be receipted `executed: true, is_error: false` — the exact slip
    this test exists to prevent.
    """
    if not (isinstance(value, ast.Call) and getattr(value.func, "id", None) == "ToolResult"):
        return False
    for kw in value.keywords:
        if kw.arg == "is_error" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
            return True
        if kw.arg == "is_error":
            return False
    return False  # is_error omitted -> defaults to False


def _returned_values(handler):
    """Every value returned from the handler (nested branches included)."""
    values = []
    for node in ast.walk(handler):
        if isinstance(node, ast.Return) and node.value is not None:
            values.append(node.value)
    return values


def _violations():
    found = []
    for path in sorted(TOOLS_CORE.rglob("*.py")):
        for fn in _tool_functions(path):
            for handler in _broad_handlers(fn):
                for value in _returned_values(handler):
                    if _is_failure_result(value):
                        continue
                    if (path.name, fn.name) in ALLOWED_SUCCESS_RETURNS:
                        continue
                    found.append(
                        f"{path.name}:{value.lineno} in {fn.name}() -> {ast.unparse(value)}"
                    )
    return found


def test_tool_result_carries_explicit_outcome():
    result = ToolResult(content="timed out", is_error=True, outcome=Outcome.TIMED_OUT)

    assert result.outcome is Outcome.TIMED_OUT


def test_tool_result_derives_outcome_from_structured_metadata():
    result = ToolResult(
        content="partial",
        structured_content={"outcome": "incomplete"},
        is_error=True,
    )

    assert result.outcome is Outcome.INCOMPLETE


def test_tool_result_rejects_success_outcome_with_error_flag():
    with pytest.raises(ValueError, match="successful outcome"):
        ToolResult(content="contradictory", is_error=True, outcome=Outcome.SUCCEEDED)


def test_tool_result_from_plain_string_is_successful():
    result = ToolResult.from_raw("ok")

    assert result.outcome is Outcome.SUCCEEDED
    assert result.is_error is False


def test_shared_outcome_vocabulary_includes_refused_and_killed():
    assert Outcome("refused") is Outcome.REFUSED
    assert Outcome("killed") is Outcome.KILLED


def test_every_tool_catch_all_reports_failure():
    """No tool *catch-all* may turn an exception into a successful string.

    Narrow handlers are out of scope here (see the module docstring), as are
    handlers with no `return` at all — those fall through to a later return
    and are reviewed by hand.

    A plain string is receipted `executed: true, is_error: false`, so a tool
    that crashed is recorded as a success — the false-success class this repo
    has fixed piecemeal. Return `ToolResult(content=..., is_error=True)`
    instead, or re-raise when the failure carries a marker (timeout/kill).
    """
    violations = _violations()
    assert not violations, (
        "tool catch-alls returning plain strings (use ToolResult(is_error=True) "
        "or re-raise a marker failure):\n  " + "\n  ".join(violations)
    )


def test_allowlist_entries_are_still_needed():
    """A stale exception in the allowlist would hide a real violation later."""
    still_violating = _violations_ignoring_allowlist()
    for entry in ALLOWED_SUCCESS_RETURNS:
        assert entry in still_violating, (
            f"allowlist entry {entry} is no longer needed — the handler now "
            "reports failure, so remove it from ALLOWED_SUCCESS_RETURNS"
        )


def _violations_ignoring_allowlist() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(TOOLS_CORE.rglob("*.py")):
        for fn in _tool_functions(path):
            for handler in _broad_handlers(fn):
                for value in _returned_values(handler):
                    if not _is_failure_result(value):
                        found.add((path.name, fn.name))
    return found


@pytest.mark.parametrize("tool_path", ["tools_custom.py", "tool_index.py"])
def test_custom_command_wrappers_report_failure_on_exception(tool_path):
    """The two custom-command wrappers follow the same convention."""
    source = (pathlib.Path(__file__).resolve().parents[2] / "src" / "sdk" / tool_path).read_text()
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            name = getattr(handler.type, "id", None) or getattr(handler.type, "attr", None)
            if name not in {"Exception", "BaseException"}:
                continue
            for value in _returned_values(handler):
                if _is_failure_result(value):
                    continue
                hits.append(f"{tool_path}:{value.lineno} -> {ast.unparse(value)}")
    assert not hits, f"custom wrapper catch-alls returning strings: {hits}"
