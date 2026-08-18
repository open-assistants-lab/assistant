"""Tests for runtime assets included in distribution artifacts."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest


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


def test_from_yaml_resolves_repo_root_config_regardless_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_settings() must find the repo-root config.yaml even when the
    process CWD is elsewhere — a missing config silently falls back to
    defaults, which previously meant a stale local model."""
    from src.config import settings as settings_module

    # Other test modules set AGENT_MODEL at import time; clear it so the
    # repo-root config.yaml is the only source for the agent model.
    for var in ("AGENT_MODEL", "AGENT_TITLE_MODEL", "SUMMARIZATION_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    settings_module._config = None
    try:
        cfg = settings_module.get_settings()
        assert cfg.agent.model == "ollama-cloud:deepseek-v4-flash:0731"
    finally:
        settings_module._config = None
