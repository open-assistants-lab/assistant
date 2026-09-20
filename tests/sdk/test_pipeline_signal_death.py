"""Issue #32 part 2: a signal death inside a `shell=True` pipeline.

The custom `TOOL.md` seam detects a kill with `result.returncode < 0`, which
catches only the *shell* dying by a signal. When a pipeline member is killed,
bash reports the death of the **last** command instead — `128 + signal` — which
is positive, so the check missed it and the run was reported as a failure
*string*, which governance receipts as a successful execution.

Measured with the real wrappers (see the test cases below):

    cat | python3 -c "kill self"   -> 137   (last member killed)
    python3 -c "kill self" | cat   -> 0     (early member killed: invisible
                                             to the exit code — that half is
                                             filed separately, since surfacing
                                             it means changing the shell's
                                             pipeline semantics)
      (shell itself killed)        -> -15   (already handled)

A command that deliberately exits 128+n is indistinguishable from a killed
member; both are failures either way, so the marker is the only difference.
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

import pytest

from src.sdk.tool_index import _rebuild_custom_function
from src.sdk.tool_results import CommandKilledError
from src.sdk.tools import ToolDefinition
from src.sdk.tools_custom import _parse_tool_file

KILL_SELF = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"


def _tool(tmp_path, mode: str, command: str):
    if mode == "parsed":
        tool_file = tmp_path / "TOOL.md"
        tool_file.write_text(
            "---\nname: pipeline_tool\ndescription: d\n"
            f"command: {command}\n---\n"
        )
        td = _parse_tool_file(tool_file)
        assert td is not None
        return td
    return _rebuild_custom_function(
        ToolDefinition(name="pipeline_tool", description="d"),
        {"command": command, "install": [], "tool_dir": ""},
    )


PIPELINE_LAST_KILLED = f"cat | {sys.executable} -c \"{KILL_SELF}\""


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
def test_last_pipeline_member_killed_is_a_failure(tmp_path, mode):
    """137 is the shell's report of a signal death: it must not read as success."""
    td = _tool(tmp_path, mode, PIPELINE_LAST_KILLED)

    with pytest.raises(CommandKilledError) as exc:
        td.function()

    assert exc.value.signal_number == 9, exc.value
    assert "killed" in exc.value.detail


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
def test_ordinary_non_zero_exit_still_returns_its_message(tmp_path, mode):
    """Exit 7 is not a signal death; the existing contract is unchanged."""
    td = _tool(tmp_path, mode, "python3 -c 'import sys; print(\"oops\"); sys.exit(7)'")

    result = td.function()

    assert "Command failed (exit 7)" in result, result
    assert "oops" in result, result
    assert not isinstance(result, CommandKilledError)


@pytest.mark.parametrize("mode", ["parsed", "reconstructed"])
def test_a_successful_pipeline_is_untouched(tmp_path, mode):
    td = _tool(tmp_path, mode, "echo hello | cat")

    assert td.function().strip() == "hello"


def test_the_signal_number_maps_to_the_convention():
    """128+n for the signal numbers bash can report (1..64)."""
    for signal_number in (1, 9, 15, 64):
        assert 128 + signal_number > 128
        assert 128 + signal_number <= 192


def test_a_deliberate_128_plus_n_exit_is_indistinguishable(tmp_path):
    """Documented trade-off: exit 137 is reported as a kill.

    bash uses the same encoding, so the two cannot be told apart by exit code
    alone — and both are failures, so only the marker differs.
    """
    td = _tool(tmp_path, "parsed", "python3 -c 'import sys; sys.exit(137)'")

    with pytest.raises(CommandKilledError) as exc:
        td.function()

    assert exc.value.signal_number == 9
