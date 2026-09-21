"""Quality-gate ratchet — see docs/quality/quality-gate-policy.md.

Ruff is a hard zero gate. mypy is a ratchet: the checked-in per-file baseline
in ``docs/quality/mypy-baseline.txt`` is the maximum a file may report. This
module keeps the parse/compare logic pure so it is unit-testable; the
``scripts/mypy_baseline.py`` entry point runs it against the real tree.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = _REPO_ROOT / "docs" / "quality" / "mypy-baseline.txt"
_ERROR_LINE = re.compile(r"^(?P<file>[^:]+):\d+: error:")


def parse_errors(output: str) -> Counter[str]:
    """Count mypy ``error:`` lines per file from raw mypy output."""
    counts: Counter[str] = Counter()
    for line in output.splitlines():
        match = _ERROR_LINE.match(line)
        if match:
            counts[match.group("file")] += 1
    return counts


def load_baseline(path: Path) -> Counter[str]:
    """Read ``file: count`` lines, ignoring comments and blank lines."""
    counts: Counter[str] = Counter()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(":")
        if not name.strip():
            continue
        counts[name.strip()] = int(value)
    return counts


def render_baseline(counts: dict[str, int] | Counter[str]) -> str:
    """Render the canonical baseline file body."""
    lines = [
        "# mypy ratchet baseline — regenerate with:",
        "#   uv run python scripts/mypy_baseline.py --write",
        "# The gate fails when a file's count grows or an unlisted file gains an error.",
    ]
    lines += [f"{path}: {count}" for path, count in sorted(counts.items())]
    return "\n".join(lines) + "\n"


def compare(
    current: dict[str, int] | Counter[str],
    baseline: dict[str, int] | Counter[str],
) -> list[str]:
    """Return one message per file whose error count grew above the baseline."""
    problems: list[str] = []
    for path in sorted(current):
        allowed = baseline.get(path, 0)
        if current[path] > allowed:
            problems.append(f"{path}: {current[path]} errors (baseline {allowed})")
    return problems


def run_mypy(target: str = "src/") -> str:
    """Run mypy in the current environment and return combined output."""
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", target],
        capture_output=True,
        text=True,
    )
    return proc.stdout + proc.stderr


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    counts = parse_errors(run_mypy())
    total = sum(counts.values())

    if "--write" in args:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(render_baseline(counts), encoding="utf-8")
        print(
            f"mypy baseline written: {total} errors in {len(counts)} files "
            f"-> {BASELINE_PATH}"
        )
        return 0

    baseline = load_baseline(BASELINE_PATH)
    problems = compare(counts, baseline)
    if problems:
        print("mypy ratchet failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Fix the new errors, or regenerate the baseline with --write if the "
            "added debt is deliberate.",
            file=sys.stderr,
        )
        return 1

    print(f"mypy ratchet passed: {total} errors (baseline {sum(baseline.values())})")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via scripts/ entry point
    raise SystemExit(main())
