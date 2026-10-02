"""Receipt outcomes must carry every terminal state (#52) and blocked output
must not survive in returned or persisted state (#60)."""

import pytest

from src.sdk.execution_models import Outcome
from src.sdk.governance import outcome_for
from src.sdk.guardrails import GuardrailResult
from src.sdk.loop import AgentLoop, RunConfig
from src.sdk.messages import Message, StreamChunk, ToolCall
from src.sdk.tools import ToolResult, tool
from tests.sdk.test_sdk_loop import MockProvider


class TestOutcomeVocabulary:
    """#52: an UNCERTAIN result must never be receipted as succeeded."""

    @pytest.mark.parametrize("outcome", list(Outcome))
    @pytest.mark.parametrize("is_error", [False, True])
    def test_every_outcome_survives_unchanged(self, outcome, is_error):
        result = {
            "content": "x",
            "structured_content": {"outcome": outcome.value, "executed": True},
            "is_error": is_error,
        }
        assert outcome_for(result) == outcome.value

    @pytest.mark.parametrize("outcome", [Outcome.UNCERTAIN, Outcome.CANCELLED, Outcome.REJECTED])
    def test_ambiguous_outcomes_never_become_success(self, outcome):
        result = {
            "content": "x",
            "structured_content": {"outcome": outcome.value, "executed": True},
            "is_error": False,
        }
        assert outcome_for(result) != "succeeded"

    def test_refusals_still_read_as_refusals(self):
        for error in ("tool disabled", "permission changed", "unknown tool"):
            result = {
                "content": "x",
                "structured_content": {"executed": False, "error": error},
                "is_error": True,
            }
            assert outcome_for(result) == "refused"

    def test_plain_success_is_still_success(self):
        assert outcome_for({"content": "x", "structured_content": {}, "is_error": False}) == "succeeded"

    @pytest.mark.asyncio
    async def test_executed_proposal_keeps_an_uncertain_outcome(self, tmp_path, monkeypatch):
        """The end-to-end path: a tool that cannot say what happened."""
        import src.sdk.governance as gov
        import src.storage.paths as paths_mod
        from src.sdk.governance import GovernanceService
        from src.sdk.tools import ToolDefinition

        monkeypatch.setattr(
            paths_mod.DataPaths,
            "root",
            property(lambda self: tmp_path / "root"),
        )
        monkeypatch.setattr(gov, "_services", {})
        svc = GovernanceService()
        monkeypatch.setattr(svc, "resolve_permission", lambda *a: "ask")

        async def ambiguous() -> ToolResult:
            return ToolResult(
                content="the request was sent but no confirmation arrived",
                structured_content={"executed": True},
                outcome=Outcome.UNCERTAIN,
            )

        td = ToolDefinition(
            name="flaky_send",
            description="sends, outcome unknown",
            parameters={"type": "object", "properties": {}},
            function=ambiguous,
        )
        monkeypatch.setattr("src.sdk.runner.get_native_tools", lambda: [td])
        pid = svc.create_pending("alice", "flaky_send", {}, permission="ask")
        svc.approve("alice", pid)
        try:
            result = await svc.execute_approved("alice", pid)
            assert result["structured_content"]["outcome"] == "uncertain"
            assert svc.resolve_pending("alice", pid)["outcome"] == "uncertain"
        finally:
            for kernel in svc._execution_kernels.values():
                await kernel._store.close()


class _AlwaysTrip:
    """An output guardrail that refuses everything."""

    def __init__(self) -> None:
        self.name = "secret_filter"
        self.calls: list[str] = []

    async def check(self, output: str, state) -> GuardrailResult:
        self.calls.append(output)
        return GuardrailResult(tripwire_triggered=True, message="sensitive output")


@tool
async def probe() -> str:
    """A tool the model calls once."""
    return "probe done"


class TestBlockedOutputIsRemoved:
    """#60: blocked content must not survive in returned or persisted state."""

    @pytest.mark.asyncio
    async def test_ordinary_answer_is_replaced_not_appended(self):
        guard = _AlwaysTrip()
        provider = MockProvider(responses=[Message.assistant(content="SECRET")])
        loop = AgentLoop(provider=provider, output_guardrails=[guard])
        result = await loop.run([Message.user("say something")])
        assert guard.calls == ["SECRET"]
        assert not any("SECRET" in str(m.content) for m in result), (
            "blocked content remained in the returned history"
        )
        assert any("Output blocked" in str(m.content) for m in result)

    @pytest.mark.asyncio
    async def test_post_nudge_final_answer_is_also_checked(self):
        """The final-answer branch skipped the guard entirely."""
        guard = _AlwaysTrip()
        provider = MockProvider(responses=[
            Message.assistant(tool_calls=[ToolCall(id="a", name="probe", arguments={})]),
            Message.assistant(content="SECRET"),
            Message.assistant(content="SECRET"),
        ])
        loop = AgentLoop(
            provider=provider,
            tools=[probe],
            output_guardrails=[guard],
            run_config=RunConfig(max_duplicate_tool_nudges=0),
        )
        result = await loop.run([Message.user("go")])
        assert guard.calls, "the post-nudge final answer bypassed the output guard"
        assert not any("SECRET" in str(m.content) for m in result)

    @pytest.mark.asyncio
    async def test_streamed_done_does_not_carry_blocked_text(self):
        guard = _AlwaysTrip()
        provider = MockProvider()
        provider.set_stream_events([
            [StreamChunk.text_delta(content="SECRET"), StreamChunk.done(content="SECRET")],
        ])
        loop = AgentLoop(provider=provider, output_guardrails=[guard])
        chunks = [c async for c in loop.run_stream([Message.user("say something")])]
        done = [c for c in chunks if c.canonical_type == "done"]
        assert done, "the stream produced no done chunk"
        assert "SECRET" not in str(done[-1].content or "")
        assert not any("SECRET" in str(m.content) for m in loop.state.messages), (
            "blocked content remained in the streamed transcript"
        )
