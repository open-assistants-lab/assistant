"""Bounded test runner — an affordance for the thing tasks ask for first.

Not a shell escape. It runs a *test command* inside a directory that the
filesystem tools already allow, and reports the result. It is deliberately NOT
annotated ``read_only``: pytest executes arbitrary code, so blanket auto-approval
would be a wider hole than ``shell_execute`` rather than a convenience.

Observed behaviour this addresses: 4 of 13 calls spent building a custom
``run_pytest`` tool (TOOL.md + reload + call) instead of using the shell, which
suggests approval friction is being routed around rather than removed.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, ToolResult, tool

logger = get_logger()

ALLOWED_RUNNERS = ("pytest", "python3 -m pytest", "python -m pytest")
_MAX_OUTPUT_CHARS = 8000
_TIMEOUT_SECONDS = 300


def _test_summary(text: str, ok: bool) -> str:
    """A short, honest pass/fail line, so the model does not have to mine output."""
    m = re.search(r"(\d+) passed", text)
    f = re.search(r"(\d+) failed", text)
    e = re.search(r"(\d+) error", text)
    passed = int(m.group(1)) if m else 0
    failed = int(f.group(1)) if f else 0
    errors = int(e.group(1)) if e else 0
    verdict = "ALL PASS" if ok and failed == 0 and errors == 0 else "FAILING"
    line = f"RESULT: {verdict} — {passed} passed, {failed} failed, {errors} errors."
    if failed or errors:
        line += " The failing test defines the expected behaviour; read it before editing."
    return line


@tool
async def run_tests(
    directory: str,
    args: str = "",
    user_id: str = "",
) -> ToolResult:
    """Run the test suite for a project, and report an honest pass/fail.

    Use this FIRST, before editing anything: the failing test states the
    expected behaviour, which is what tells you what to change. Call it again
    after every edit so you know whether you actually fixed it.

    The command runs inside an allowed root only. It is not auto-approved;
    it uses the same approval gate as the shell.

    Args:
        directory: Absolute path of the project to test. Must be under an
            allowed root (filesystem.allowed_roots or the data root).
        args: Extra pytest arguments, e.g. "-k test_retry" or "-q"
        user_id: User identifier (injected automatically)

    Returns:
        The suite outcome with a RESULT line first, then the output tail.
    """
    import asyncio

    if not user_id:
        user_id = "default_user"

    from src.sdk.tools_core.filesystem import _allowed_roots  # noqa: SLF001 - same subsystem

    paths = __import__(
        "src.storage.paths", fromlist=["get_paths"]
    ).get_paths(user_id)
    roots = _allowed_roots(user_id, paths)

    target = Path(directory).expanduser()
    if not target.exists():
        return ToolResult(
            content=f"Error: directory does not exist: {directory}",
            is_error=True,
            structured_content={"status": "failed", "reason": "missing_directory"},
        )
    resolved = target.resolve()
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        allowed = ", ".join(str(r) for r in roots)
        return ToolResult(
            content=(
                f"Error: {directory} is not under an allowed root.\n"
                f"Allowed roots: {allowed}.\n"
                "Add it to filesystem.allowed_roots to test it."
            ),
            is_error=True,
            structured_content={"status": "failed", "reason": "outside_allowed_roots"},
        )

    # Run through the sandbox seam. Not auto-approved: pytest executes arbitrary
    # code, so read_only=True would be a wider hole than shell_execute.
    started = __import__("time").monotonic()
    from src.sdk.sandbox import SandboxLimits, get_sandbox_backend

    backend = get_sandbox_backend()
    try:
        result = await asyncio.to_thread(
            backend.run,
            ["python3", "-m", "pytest", "-v", str(resolved)],
            str(resolved),
            SandboxLimits(
                timeout_seconds=_TIMEOUT_SECONDS,
                max_output_bytes=200_000,
                max_write_bytes=8 * 1024 * 1024,
            ),
        )
    except Exception as exc:
        logger.error("run_tests.failed", {"error": str(exc)}, user_id=user_id)
        return ToolResult(
            content=f"Error running tests: {type(exc).__name__}: {exc}",
            is_error=True,
            structured_content={"status": "failed", "reason": "exception"},
        )

    # SandboxResult exposes stdout/stderr/timed_out/signalled — no returncode.
    # So the pass/fail verdict comes from pytest's own summary, and the kill
    # flags handle the abnormal cases. Defaulting returncode to 1 here made a
    # passing suite report FAILING.
    text = str(getattr(result, "stdout", "") or "") + str(getattr(result, "stderr", "") or "")
    text = text[:_MAX_OUTPUT_CHARS]
    timed_out = bool(getattr(result, "timed_out", False))
    signalled = bool(getattr(result, "signalled", False))
    ok = (not timed_out) and (not signalled) and bool(
        re.search(r"\bno tests ran\b|\d+ passed", text)
    ) and not re.search(r"\d+ failed|\d+ error\b", text)
    summary = _test_summary(text, ok)
    return ToolResult(
        content=f"{summary}\n\n--- output ---\n{text}",
        is_error=not ok,
        structured_content={
            "status": "passed" if ok else "failed",
            "timed_out": timed_out,
            "directory": str(resolved),
            "seconds": round(__import__("time").monotonic() - started, 2),
        },
    )

run_tests.annotations = ToolAnnotations(
    title="Run the Test Suite",
    destructive=False,
    open_world=False,
)
