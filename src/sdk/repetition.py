"""Cap identical tool calls within a run.

Two different things need bounding, and the loop already has one of them:

* **Identical ``(tool, arguments)``** — the loop's existing US-003 duplicate
  guard handles this, so it is not duplicated here.
* **The same tool over and over with different arguments** — probing. The
  duplicate guard does not see this, and it is the expensive case: observed live
  at 32 ``files_list`` calls walking different subdirectories, and 7
  ``web_fetch`` calls on the same file via different URLs, ending in
  ``max_tokens_total exceeded`` with no answer at all.

This module covers the second case with a per-tool budget, and also offers an
exact-args mode for callers that want it. Both are counted separately so the
error can say which limit was hit.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any


class RepetitionLimitReached(RuntimeError):  # noqa: N818 - public contract name
    """Raised when one identical call has been made too many times."""

    def __init__(self, tool: str, call_args: str, count: int) -> None:
        self.tool = tool
        # NOT self.args: BaseException.args is a tuple, and shadowing it with a
        # str breaks exception pickling and reads very strangely in tracebacks.
        self.call_args = call_args
        self.count = count
        super().__init__(
            f"Stopped: `{tool}` was called {count} times with identical arguments "
            f"({call_args[:120]}). The same call returns the same result, so "
            "continuing cannot make progress. Vary the arguments, use a "
            "different tool, or report what you have."
        )


def normalize_args(args: Any) -> str:
    """Stable string form so key order does not disguise a repeat."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (TypeError, ValueError):
            return str(args).strip()
    try:
        return str(json.dumps(args, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return str(args)


class RepetitionGuard:
    """Bound both exact repeats and per-tool probing within a run.

    ``limit`` is the number of calls *allowed*; the next one trips the guard.
    """

    def __init__(self, limit: int = 3, per_tool_limit: int | None = None) -> None:
        self.limit = max(1, int(limit))
        self.per_tool_limit = max(1, int(per_tool_limit if per_tool_limit is not None else limit * 4))
        self._exact: Counter[tuple[str, str]] = Counter()
        self._per_tool: Counter[str] = Counter()

    def check(self, tool: str, args: Any) -> None:
        """Record a call; raise when a limit is exceeded."""
        key = (tool, normalize_args(args))
        self._exact[key] += 1
        if self._exact[key] > self.limit:
            raise RepetitionLimitReached(tool, key[1], self._exact[key])
        self._per_tool[tool] += 1
        if self._per_tool[tool] > self.per_tool_limit:
            raise RepetitionLimitReached(
                tool,
                f"<{self._per_tool[tool]} calls in this run, different arguments>",
                self._per_tool[tool],
            )

    def count_for(self, tool: str, args: Any) -> int:
        return self._exact[(tool, normalize_args(args))]

    def calls_to(self, tool_name: str) -> int:
        """How many times this tool has been called this run, any arguments."""
        return self._per_tool[tool_name]

    def reset(self) -> None:
        self._exact.clear()
        self._per_tool.clear()
