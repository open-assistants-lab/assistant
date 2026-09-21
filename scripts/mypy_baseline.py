#!/usr/bin/env python3
"""mypy ratchet entry point — see docs/quality/quality-gate-policy.md.

Usage:
    uv run python scripts/mypy_baseline.py           # check (exit 1 on regression)
    uv run python scripts/mypy_baseline.py --write   # regenerate the baseline
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.quality_gate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
