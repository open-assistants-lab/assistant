"""Tests for RunOutcome and ImprovementSuggestion storage."""

import pytest
from pathlib import Path
from src.sdk.loops.storage import LoopEngineeringDB, RunOutcome


@pytest.mark.asyncio
async def test_persist_and_read_run_outcome(tmp_path):
    store = LoopEngineeringDB(tmp_path / "loop_engineering.db")
    await store.init()

    outcome = RunOutcome(
        run_id="run_1",
        user_id="alice",
        session_id="s1",
        trigger_type="manual",
        response="hello",
        verification_status="satisfied",
        verification_iterations=1,
        verification_evaluations=[{"iteration": 0, "result": "satisfied"}],
        cost_usd=0.01,
        input_tokens=100,
        output_tokens=50,
        model="ollama-cloud:deepseek-v4-flash",
        timestamp="2026-07-27T10:00:00Z",
    )
    await store.save_run_outcome(outcome)

    outcomes = await store.list_run_outcomes("alice", limit=10)
    assert len(outcomes) == 1
    assert outcomes[0].run_id == "run_1"
    assert outcomes[0].verification_status == "satisfied"
    assert outcomes[0].verification_evaluations[0]["result"] == "satisfied"


@pytest.mark.asyncio
async def test_persist_and_read_improvement_suggestion(tmp_path):
    from src.sdk.loops.storage import ImprovementSuggestion

    store = LoopEngineeringDB(tmp_path / "loop_engineering.db")
    await store.init()

    suggestion = ImprovementSuggestion(
        suggestion_id="sug_1",
        run_id="run_1",
        target_type="tool_description",
        target_name="files_read",
        current_value="Read a file",
        proposed_value="Read a file from disk. Supports text files.",
        rationale="Grader repeatedly fails on file reading tasks",
        risk_level="low",
        status="proposed",
        created_at="2026-07-27T10:00:00Z",
    )
    await store.save_suggestion(suggestion)

    suggestions = await store.list_suggestions(status="proposed")
    assert len(suggestions) == 1
    assert suggestions[0].suggestion_id == "sug_1"
    assert suggestions[0].risk_level == "low"


@pytest.mark.asyncio
async def test_update_suggestion_status(tmp_path):
    from src.sdk.loops.storage import ImprovementSuggestion

    store = LoopEngineeringDB(tmp_path / "loop_engineering.db")
    await store.init()

    suggestion = ImprovementSuggestion(
        suggestion_id="sug_2",
        run_id="run_1",
        target_type="rubric",
        target_name="default",
        current_value="old",
        proposed_value="new",
        rationale="test",
        risk_level="medium",
        created_at="2026-07-27T10:00:00Z",
    )
    await store.save_suggestion(suggestion)

    success = await store.update_suggestion_status("sug_2", "approved", "2026-07-27T11:00:00Z")
    assert success is True

    suggestions = await store.list_suggestions(status="approved")
    assert len(suggestions) == 1
    assert suggestions[0].status == "approved"