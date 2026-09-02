"""Issue #12 regression: governance guards must run on the streaming path.

A hard_block-tier tool called via a streamed loop must NOT execute; the
model receives a synthetic governance refusal instead.
"""

from __future__ import annotations

import pytest

from src.sdk.governance import GovernanceService


@pytest.fixture()
def gov_svc(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod

    monkeypatch.setattr(
        paths_mod.DataPaths,
        "root",
        property(lambda self: tmp_path / "root"),
    )
    import src.sdk.governance as gov

    monkeypatch.setattr(gov, "_services", {})
    monkeypatch.setattr(gov, "governance_enabled", lambda: True)
    return GovernanceService()


@pytest.mark.asyncio
async def test_hard_block_tool_never_executes_on_stream(gov_svc, monkeypatch):
    from src.sdk.loop import AgentLoop
    from src.sdk.messages import Message

    executed: list[str] = []

    import json as _json

    from src.sdk.messages import StreamChunk

    class Provider:
        def chat_stream(self, messages, tools=None, model=None, **kwargs):
            async def _stream():
                yield StreamChunk.tool_input_start(tool="gated_tool", call_id="call_1")
                yield StreamChunk.tool_input_delta(
                    call_id="call_1", content=_json.dumps({"x": "1"})
                )
                yield StreamChunk.tool_input_end(call_id="call_1", tool="gated_tool")
                yield StreamChunk.done(content="")

            return _stream()

    monkeypatch.setattr(
        "src.sdk.governance.get_governance_service", lambda user_id=None: gov_svc
    )
    # Tier via capabilities profile (plan M4-1 tier source).
    import src.sdk.capabilities as caps_mod

    monkeypatch.setattr(
        caps_mod,
        "load_capabilities",
        lambda root: {"governance_tiers": {"gated_tool": "hard_block"}},
    )

    from src.sdk.tools import tool

    @tool(name="gated_tool")
    async def gated_tool(x: str = "") -> str:  # pragma: no cover - must not run
        """Gated tool (hard_block tier)."""
        executed.append(x)
        return "EXECUTED"

    from src.sdk.middleware_hitl import HITLMiddleware

    loop = AgentLoop(
        provider=Provider(),
        tools=[gated_tool],
        user_id="stream_user",
        run_config=None,
        middlewares=[HITLMiddleware(user_id="stream_user")],
    )
    loop._flow_model = "test-model"

    chunks = [c async for c in loop.run_stream([Message.user("go")])]

    assert executed == []  # the tool body never ran
    previews = [str(getattr(c, "result_preview", "")) for c in chunks]
    assert any("governance" in p and "blocked" in p for p in previews)
