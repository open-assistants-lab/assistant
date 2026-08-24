"""Tests for conditional system-prompt guidelines (Pi-style).

The memory-recall strategy section only lists tools that are actually
enabled, and a shell-based file-ops guideline only appears when the
dedicated file-search tools are unavailable.
"""

from __future__ import annotations

from unittest.mock import patch

from src.sdk import runner

MEMORY_TOOLS = [
    "message_search",
    "message_count",
    "message_timeline",
    "memory_profile",
]


def _prompt(caps: dict) -> str:
    with (
        patch("src.sdk.runner._load_user_capabilities", return_value=caps),
        patch("src.sdk.runner._get_user_prompt_context", return_value=""),
        patch("src.sdk.runner._get_skills_context", return_value=""),
        patch("src.sdk.runner._ensure_prompt_seeded"),
    ):
        return runner._get_system_prompt("test_user")


def _disabled(*names: str) -> dict:
    return {"tools": {name: False for name in names}}


def test_memory_context_includes_all_tools_by_default():
    prompt = _prompt({})
    assert "## Memory Recall Strategy" in prompt
    for name in MEMORY_TOOLS:
        assert f"**{name}**" in prompt


def test_memory_context_omits_disabled_tools():
    prompt = _prompt(_disabled("message_search", "memory_profile"))
    assert "**message_search**" not in prompt
    assert "**memory_profile**" not in prompt
    assert "**message_count**" in prompt
    assert "**message_timeline**" in prompt


def test_memory_context_absent_when_all_tools_disabled():
    prompt = _prompt(_disabled(*MEMORY_TOOLS))
    assert "Memory Recall Strategy" not in prompt


def test_no_file_ops_guideline_when_any_dedicated_file_tool_enabled():
    """T4: files_read enabled must suppress shell file-ops guidance even when
    the dedicated SEARCH tools are off (persona round regression)."""
    prompt = _prompt(_disabled("files_glob_search", "files_grep_search"))
    assert "Use shell_execute for file operations" not in prompt


def test_file_ops_guideline_only_when_all_file_tools_disabled():
    """Legacy path: guidance only appears when every dedicated file tool is
    disabled and shell remains."""
    caps = _disabled(
        "files_read", "files_list", "files_glob_search", "files_grep_search"
    )
    prompt = _prompt(caps)
    assert "Use shell_execute for file operations" in prompt


def test_no_file_ops_guideline_when_file_search_enabled():
    prompt = _prompt({})
    assert "Use shell_execute for file operations" not in prompt


def test_no_file_ops_guideline_when_shell_disabled():
    prompt = _prompt(_disabled("shell_execute", "files_glob_search", "files_grep_search"))
    assert "Use shell_execute for file operations" not in prompt


def test_preferences_include_inspection_priority_when_both_families_enabled():
    """T4: with shell + files tools co-enabled, an explicit priority rule must
    tell the model shell is never for file inspection."""
    prefs = runner._build_tool_preferences({})
    assert "never use shell_execute to read, list, or search files" in prefs
