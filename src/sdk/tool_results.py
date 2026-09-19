"""Bounded custom-command previews and scoped, non-executing result recovery."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import src.storage.paths as _paths

PAGE_CHARS = 5000
RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_RESULTS = 100
_RESULT_ID = re.compile(r"[0-9a-f]{32}\Z")


def _directory(user_id: str, workspace_id: str) -> Path:
    paths = _paths.get_paths(user_id, workspace_id=workspace_id)
    root = paths.user_dir.resolve()
    scope = hashlib.sha256(workspace_id.encode()).hexdigest()
    directory = root / ".tool_results" / scope
    # Do not follow workspace-controlled symlinks into other users' stores.
    for part in (directory.parent, directory):
        if part.is_symlink():
            raise OSError("Result directory is a symlink")
    return directory


def format_output(output: str, user_id: str, workspace_id: str) -> str:
    """Keep small results unchanged; persist large ones before advertising recovery."""
    if len(output) <= PAGE_CHARS:
        return output or "(no output)"
    envelope: dict[str, Any] = {
        "content": output[:PAGE_CHARS],
        "truncated": True,
        "total_chars": len(output),
        "end": PAGE_CHARS,
    }
    try:
        directory = _directory(user_id, workspace_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        now = time.time()
        files: list[tuple[float, Path]] = []
        for candidate in directory.glob("*.result"):
            try:
                files.append((candidate.lstat().st_mtime, candidate))
            except FileNotFoundError:
                continue  # Another save may have evicted this entry already.
        files.sort()
        for i, (modified, old) in enumerate(files):
            if modified < now - RETENTION_SECONDS or i <= len(files) - MAX_RESULTS:
                old.unlink(missing_ok=True)
        result_id = uuid.uuid4().hex
        path = directory / f"{result_id}.result"
        try:
            with path.open("xb") as stream:
                os.chmod(path, 0o600)
                # Fixed-width encoding permits bounded, character-indexed reads,
                # even for multibyte Unicode and embedded NUL characters.
                stream.write(output.encode("utf-32-le"))
        except OSError:
            path.unlink(missing_ok=True)
            raise
        envelope.update(
            result_id=result_id,
            recovery="Use tool_result_read with result_id and offset=end; do not rerun the command. "
            "Results expire after 7 days or eviction (100 results per workspace).",
        )
    except (OSError, ValueError):
        envelope["error"] = "Full output was not saved; recovery is unavailable."
    return json.dumps(envelope, ensure_ascii=False)


def read_result(
    result_id: str, offset: int, limit: int, user_id: str, workspace_id: str,
) -> dict[str, Any]:
    """Read at most one bounded page. Never executes a command or accepts a path."""
    if not isinstance(result_id, str) or not _RESULT_ID.fullmatch(result_id):
        return {"error": "Invalid result ID."}
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= PAGE_CHARS:
        return {"error": f"offset must be nonnegative; limit must be between 1 and {PAGE_CHARS}."}
    try:
        path = _directory(user_id, workspace_id) / f"{result_id}.result"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            stat = os.fstat(stream.fileno())
            if stat.st_mtime < time.time() - RETENTION_SECONDS:
                return {"error": "Result not found or expired."}
            total = stat.st_size // 4
            if offset > total:
                return {"error": "Offset exceeds result length."}
            stream.seek(offset * 4)
            content = stream.read(limit * 4).decode("utf-32-le")
        end = offset + len(content)
        return {
            "result_id": result_id, "content": content, "offset": offset,
            "end": end, "total_chars": total, "has_more": end < total,
        }
    except (OSError, ValueError):
        return {"error": "Result not found or expired."}


TIMEOUT_MARKER = "timed_out"
KILLED_MARKER = "killed"


class CommandKilledError(RuntimeError):
    """A command was killed by a signal before it finished.

    Distinct from `subprocess.TimeoutExpired`: the sandbox's own cap did not
    fire, the child died from a resource limit (RLIMIT_AS/CPU/NPROC/FSIZE) or
    another signal.
    """

    def __init__(self, command: str, signal_number: int | None, detail: str) -> None:
        super().__init__(detail)
        self.command = command
        self.signal_number = signal_number
        self.detail = detail


def raise_command_killed(
    command: str, signal_number: int | None, elapsed: float
) -> None:
    """Raise the distinct failure for a signal-killed command (issue #25).

    A child killed by a resource limit exits with a *negative* code and
    `timed_out=False`, which is otherwise indistinguishable from an ordinary
    non-zero exit — so a killed run was receipted `executed: true`. Timeouts
    also report `exit_code=-1`, so tools must read the sandbox's explicit
    `signalled` flag rather than the sign of the code.
    """
    name = f"signal {signal_number}" if signal_number else "a signal"
    if signal_number:
        try:
            import signal as _signal

            name = f"{_signal.Signals(signal_number).name} ({signal_number})"
        except (ValueError, AttributeError):
            pass
    raise CommandKilledError(
        command, signal_number, f"{KILLED_MARKER} by {name} after {elapsed:.1f}s"
    )

def raise_command_timeout(command: str, timeout: float | None, started: float) -> None:
    """Surface a cap-killed command as failure, never as a successful return.

    The message carries the elapsed seconds so a caller (or the governance
    executor) can report a timeout receipt instead of claiming execution.
    """
    raise_timeout(command, timeout, time.monotonic() - started)


def raise_timeout(command: str, timeout: float | None, elapsed: float) -> None:
    """Raise the distinct timeout failure from an already-measured elapsed.

    Sandbox-backed tools receive `timed_out=True` from the transport seam
    rather than a `TimeoutExpired` of their own (#24), so they report the
    elapsed duration directly instead of taking a monotonic start stamp.
    """
    limit = "unbounded" if timeout is None else f"{timeout:g}s"
    raise subprocess.TimeoutExpired(
        command,
        timeout or 0,
        output=f"{TIMEOUT_MARKER}: cap reached after {elapsed:.1f}s (limit {limit})",
    )
