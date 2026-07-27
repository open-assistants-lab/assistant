"""Tests for AnalysisJob."""

import json

import pytest

from src.sdk.loops.improvement import AnalysisJob
from src.sdk.loops.storage import LoopEngineeringDB, RunOutcome
from src.sdk.messages import Message


class FakeAnalysisProvider:
    def __init__(self, response_json: str):
        self._json = response_json

    async def chat(self, messages, tools=None, model=None, provider_options=None, **kwargs):
        return Message.assistant(content=self._json)

    async def chat_stream(self, *args, **kwargs):
        raise NotImplementedError

    async def list_models(self):
        return []


@pytest.mark.asyncio
async def test_analysis_job_proposes_suggestions(tmp_path):
    db = LoopEngineeringDB(tmp_path / "db.db")
    await db.init()

    await db.save_run_outcome(RunOutcome(
        run_id="r1", user_id="alice", session_id="s1", trigger_type="manual",
        response="bad response", verification_status="needs_revision",
        verification_iterations=3, verification_evaluations=[],
        cost_usd=0.01, input_tokens=100, output_tokens=50,
        model="test", timestamp="2026-07-27T10:00:00Z",
    ))

    suggestions_json = json.dumps([
        {
            "run_id": "r1",
            "target_type": "tool_description", "target_name": "files_read",
            "current_value": "Read", "proposed_value": "Read a file from disk",
            "rationale": "Grader failed", "risk_level": "low",
        }
    ])

    provider = FakeAnalysisProvider(suggestions_json)
    job = AnalysisJob(analysis_provider=provider, mode="human_review")

    suggestions = await job.run("alice", outcome_store=db, suggestion_store=db)

    assert len(suggestions) == 1
    assert suggestions[0].target_type == "tool_description"
    assert suggestions[0].risk_level == "low"
    assert suggestions[0].status == "proposed"


@pytest.mark.asyncio
async def test_analysis_job_auto_applies_low_risk(tmp_path):
    db = LoopEngineeringDB(tmp_path / "db.db")
    await db.init()

    await db.save_run_outcome(RunOutcome(
        run_id="r2", user_id="alice", session_id="s1", trigger_type="manual",
        response="bad", verification_status="needs_revision",
        verification_iterations=2, verification_evaluations=[],
        cost_usd=0.01, input_tokens=10, output_tokens=5,
        model="test", timestamp="2026-07-27T10:00:00Z",
    ))

    suggestions_json = json.dumps([
        {
            "run_id": "r2",
            "target_type": "tool_description", "target_name": "files_read",
            "current_value": "old", "proposed_value": "new",
            "rationale": "fix", "risk_level": "low",
        },
        {
            "run_id": "r2",
            "target_type": "system_prompt", "target_name": "main",
            "current_value": "old", "proposed_value": "new",
            "rationale": "fix", "risk_level": "high",
        }
    ])

    provider = FakeAnalysisProvider(suggestions_json)
    job = AnalysisJob(analysis_provider=provider, mode="auto_apply", auto_apply_risk_threshold="low")

    suggestions = await job.run("alice", outcome_store=db, suggestion_store=db)

    assert len(suggestions) == 2
    low_risk = [s for s in suggestions if s.risk_level == "low"]
    high_risk = [s for s in suggestions if s.risk_level == "high"]
    assert low_risk[0].status == "applied"
    assert high_risk[0].status == "proposed"


@pytest.mark.asyncio
async def test_analysis_job_no_outcomes_returns_empty(tmp_path):
    db = LoopEngineeringDB(tmp_path / "db.db")
    await db.init()

    provider = FakeAnalysisProvider("[]")
    job = AnalysisJob(analysis_provider=provider)

    suggestions = await job.run("alice", outcome_store=db, suggestion_store=db)
    assert suggestions == []
