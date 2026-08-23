"""Tests for shell output spill-to-file (Pi-style truncation).

When shell output exceeds the configured budget, the truncated preview in
context points at a file containing the full output (readable via files_read).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.sdk.tools_core.shell import shell_execute


def _small_config() -> dict:
    return {
        "allowed_commands": {"python3", "echo"},
        "timeout_seconds": 30,
        "max_output_kb": 1,  # tiny budget to force truncation
    }


def test_truncated_output_spills_to_file(tmp_path):
    with (
        patch("src.sdk.tools_core.shell._get_shell_config", return_value=_small_config()),
        patch("src.sdk.tools_core.shell._get_root_path", return_value=tmp_path),
    ):
        result = shell_execute.invoke(
            {"command": "python3 -c print('x'*5000)", "user_id": "test"}
        )

    assert "truncated" in result
    assert "Full output:" in result
    path = result.split("Full output:")[-1].strip()
    full = Path(path).read_text(encoding="utf-8")
    assert len(full) >= 5000
    assert "xxxxx" in full
    # The spill file lives under the workspace files dir (agent-readable)
    assert str(tmp_path) in path


def test_small_output_not_truncated_no_spill(tmp_path):
    with (
        patch("src.sdk.tools_core.shell._get_shell_config", return_value=_small_config()),
        patch("src.sdk.tools_core.shell._get_root_path", return_value=tmp_path),
    ):
        result = shell_execute.invoke({"command": "echo hello", "user_id": "test"})

    assert "hello" in result
    assert "Full output:" not in result
    assert not (tmp_path / ".shell_output").exists()
