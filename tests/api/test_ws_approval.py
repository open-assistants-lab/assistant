"""WS HITL approval-path regression tests (audit B4).

Background
----------
The WS approve flows used to re-run the agent with an instruction-only
message ("approve: please proceed with {tool}") and rely on the model
re-proposing the EXACT approved call. Each generation produces fresh call
ids/args, so the loop's approval match never fired and every re-run
interrupted again — an approve loop. The fix routes all approval paths
through `conversation._execute_approved_tool`, which executes the pending
call directly and puts the real outcome in the model's context.

Why these tests are shaped this way:
- `AgentLoop._should_interrupt` is hard-disabled (src/sdk/loop.py) with HITL
  kept dormant for ship, so the loop never emits `interrupt` events and the
  WS `pending_container` can never be populated at runtime today. A live
  WS integration test (drive approve -> assert exactly one interrupt)
  is impossible without re-enabling HITL — out of scope for this fix.
- The regression is therefore pinned two ways:
  1. a behavioral test of `_execute_approved_tool` itself (edited args must
     be executed under the ORIGINAL call_id, and the continuation must be
     the assistant+tool_result+user triple, not an instruction-only nudge);
  2. a source-contract test asserting the instruction-only retry patterns
     are gone from ws.py and every approval site routes through the helper.
     This is a tripwire for reintroduction of the loop bug.
"""

import pathlib

import pytest

from src.http.routers.conversation import _execute_approved_tool


class FakeLoop:
    """Minimal AgentLoop stand-in: records the executed ToolCall."""

    def __init__(self) -> None:
        self.executed: object = None

    async def _execute_tool(self, tc):
        self.executed = tc
        return _FakeResult("wrote file /x")


class _FakeResult:
    def __init__(self, content: str) -> None:
        self.content = content


@pytest.mark.asyncio
async def test_execute_approved_tool_executes_edited_args_with_original_call_id():
    """Edited args must be executed under the ORIGINAL pending call_id."""
    loop = FakeLoop()
    edited_args = {"path": "/x", "content": "edited content"}
    messages = await _execute_approved_tool(loop, "files_write", edited_args, "call_orig_1")

    assert loop.executed is not None
    assert loop.executed.name == "files_write"
    assert loop.executed.arguments == edited_args
    assert loop.executed.id == "call_orig_1"

    # Continuation triple: assistant (with the executed call) + tool_result +
    # a user continuation — NOT an instruction-only nudge asking the model
    # to re-propose the tool.
    assert len(messages) == 3
    assert messages[0].role == "assistant"
    assert messages[0].tool_calls[0].id == "call_orig_1"
    assert messages[1].role == "tool"
    assert messages[1].tool_call_id == "call_orig_1"
    assert "wrote file /x" in messages[1].content
    assert messages[2].role == "user"
    assert "approved files_write" in messages[2].content


def test_ws_approval_sites_route_through_execute_approved_tool():
    """The WS approval/edit paths must use the direct-execution helper.

    Fails on reintroduction of the instruction-only retry pattern that
    caused the B4 approve loop. Verifies:
    - the old nudge strings are gone;
    - every approval site (top-level ApproveMessage + the three sites fixed
      by this task) calls `_execute_approved_tool(...)`.
    """
    ws_path = pathlib.Path(__file__).resolve().parents[2] / "src/http/routers/ws.py"
    text = ws_path.read_text()

    # Instruction-only retry patterns (the bug) must not exist anywhere.
    assert "approve: please proceed with " not in text
    assert "approved: proceed with " not in text

    # Top-level ApproveMessage (already migrated) + EditAndApproveMessage +
    # deferred-control approve + deferred-control edit-and-approve.
    assert text.count("_execute_approved_tool(") >= 4
