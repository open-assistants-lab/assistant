"""Tests for runtime assets included in distribution artifacts."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path


def test_wheel_contains_exact_grader_prompt_seed(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    source = project_root / "seeds" / "prompts" / "grader_prompt.md"

    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )

    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        assert wheel.read("seeds/prompts/grader_prompt.md") == source.read_bytes()
