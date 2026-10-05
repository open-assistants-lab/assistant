"""Fake the sandbox's process seam for custom-tool tests.

The soft sandbox runs commands with Popen plus explicit process-GROUP cleanup
(issue #118): a timeout must stop descendants too, which subprocess.run cannot
do (it kills only the direct child). Tests that pin "the declared timeout
reaches the process call" or simulate a kill/timeout therefore patch the Popen
surface through this helper instead of subprocess.run.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from unittest.mock import patch


class FakeProcess:
    """The slice of Popen the sandbox uses."""

    def __init__(
        self,
        argv: list[str],
        outcome: tuple,
        sink: list[tuple[list[str], float | None]],
    ) -> None:
        self.argv = list(argv)
        # A pid that does not exist: os.getpgid() fails, so the sandbox falls
        # back to kill() exactly as it would for an already-exited child.
        self.pid = 999_999_999
        self.returncode: int | None = None
        self._outcome = outcome
        self._sink = sink

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self._sink.append((list(self.argv), timeout))
        if self._outcome[0] == "timeout":
            raise subprocess.TimeoutExpired(
                self.argv, timeout or 0.0, output="", stderr=""
            )
        _, rc, out, err = self._outcome
        self.returncode = rc
        return out, err

    def kill(self) -> None:  # pragma: no cover - exercised through the sandbox
        return None


@contextmanager
def fake_sandbox(
    results: Iterable[tuple[int, str, str]] = ((0, "", ""),),
    *,
    timeout_for: Callable[[list[str]], bool] | None = None,
):
    """Patch subprocess.Popen; yields the recorded ``(argv, timeout)`` pairs.

    results: per-call ``(returncode, stdout, stderr)``; the last is reused.
    timeout_for: ``callable(argv) -> bool``; matching calls raise
        ``subprocess.TimeoutExpired`` from ``communicate()`` instead.
    """
    result_list = list(results)
    calls: list[tuple[list[str], float | None]] = []
    index = {"n": 0}

    def factory(argv, **kwargs):  # noqa: ANN001, ANN003 - mirrors Popen
        i = min(index["n"], len(result_list) - 1)
        index["n"] += 1
        rc, out, err = result_list[i]
        argv_list = list(argv)
        outcome: tuple = (
            ("timeout",)
            if (timeout_for is not None and timeout_for(argv_list))
            else ("result", rc, out, err)
        )
        return FakeProcess(argv_list, outcome, calls)

    # Patch ONLY the sandbox module's view: a global Popen patch would also
    # intercept subprocess.run's own use of Popen (the `which` probe), which is
    # unrelated to this seam.
    class _SubprocessShim:
        Popen = staticmethod(factory)

        def __getattr__(self, name):  # TimeoutExpired, PIPE, … delegate
            return getattr(subprocess, name)

    with patch("src.sdk.sandbox.subprocess", _SubprocessShim()):
        yield calls
