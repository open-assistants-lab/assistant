"""A tool-body catch-all must report failure, not a plain string.

Three times a deliberate failure was swallowed by a tool's own
``except Exception: return f"...{e}"`` and reported as a successful run (#23
`shell_execute`, #24/#25 the custom `TOOL.md` wrappers). The mechanism is the
shape of the handler, so this walks the AST of every tool body and asserts the
convention directly.

``ALLOWED_SUCCESS_RETURNS`` lists handlers that legitimately return guidance
text from a broad handler because the tool completed its job. Keep it short:
each entry is a documented exception, not a parking space.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

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


def _returned_value(handler):
    for node in ast.walk(handler):
        if isinstance(node, ast.Return) and node.value is not None:
            return node.value
    return None


def _violations():
    found = []
    for path in sorted(TOOLS_CORE.rglob("*.py")):
        for fn in _tool_functions(path):
            for handler in _broad_handlers(fn):
                value = _returned_value(handler)
                if value is None:
                    continue
                if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "ToolResult":
                    continue
                if (path.name, fn.name) in ALLOWED_SUCCESS_RETURNS:
                    continue
                found.append(f"{path.name}:{handler.lineno} in {fn.name}() -> {ast.unparse(value)}")
    return found


def test_every_tool_catch_all_reports_failure():
    """No tool may turn an exception into a successful string return.

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


def test_allowlist_entries_still_exist():
    """A stale exception in the allowlist would hide a real violation later."""
    seen = set()
    for path in sorted(TOOLS_CORE.rglob("*.py")):
        for fn in _tool_functions(path):
            for handler in _broad_handlers(fn):
                if _returned_value(handler) is not None:
                    seen.add((path.name, fn.name))
    for entry in ALLOWED_SUCCESS_RETURNS:
        assert entry in seen, f"allowlist entry {entry} no longer matches a handler"


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
            value = _returned_value(handler)
            if value is None:
                continue
            if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "ToolResult":
                continue
            hits.append(f"{tool_path}:{handler.lineno} -> {ast.unparse(value)}")
    assert not hits, f"custom wrapper catch-alls returning strings: {hits}"
