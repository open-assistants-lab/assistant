"""Tests for autoresearch ResearchTarget protocol and ResearchLoop."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.sdk.research import (
    ExperimentResult,
    PromptTarget,
    ResearchLoop,
    ResearchTarget,
)


class TestExperimentResult:
    def test_creation(self):
        r = ExperimentResult(
            target_name="test",
            metric_value=0.95,
            metric_name="accuracy",
            improved=True,
            commit_hash="abc1234",
            description="Increased temperature to 0.8",
        )
        assert r.improved is True
        assert r.metric_value == 0.95


class TestResearchTarget:
    def test_protocol_has_required_methods(self):
        """Protocol check — any class with get/set/eval/rollback is a ResearchTarget."""
        assert hasattr(ResearchTarget, "get_current")
        assert hasattr(ResearchTarget, "apply_change")
        assert hasattr(ResearchTarget, "evaluate")
        assert hasattr(ResearchTarget, "rollback")

    def test_prompt_target(self):
        """PromptTarget reads/writes from a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_file = Path(tmpdir) / "AGENTS.md"
            prompt_file.write_text("You are a helpful assistant.")

            target = PromptTarget(prompt_file)
            current = target.get_current()
            assert "helpful assistant" in current

            target.apply_change("You are a pirate.")
            assert prompt_file.read_text() == "You are a pirate."

            target.rollback()
            assert "helpful assistant" in prompt_file.read_text()


class TestResearchLoop:
    @pytest.mark.asyncio
    async def test_run_experiment_keep_on_improvement(self):
        """When evaluate() returns a better metric, the change is kept."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = AsyncMock(spec=ResearchTarget)
            target.get_current.return_value = "baseline"
            target.evaluate.side_effect = [0.8, 0.9]

            loop = ResearchLoop(target=target, experiment_dir=Path(tmpdir))
            result = await loop.run_experiment("Increase specificity")

            assert result.improved is True
            assert result.metric_value == 0.9
            target.apply_change.assert_called_once()
            target.rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_experiment_discard_on_regression(self):
        """When evaluate() returns a worse metric, the change is discarded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = AsyncMock(spec=ResearchTarget)
            target.get_current.return_value = "baseline"
            target.evaluate.side_effect = [0.8, 0.7]

            loop = ResearchLoop(target=target, experiment_dir=Path(tmpdir))
            result = await loop.run_experiment("Made it worse")

            assert result.improved is False
            assert result.metric_value == 0.7
            target.apply_change.assert_called_once()
            target.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_experiment_logs_to_tsv(self):
        """Results are logged to results.tsv in the experiment directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = AsyncMock(spec=ResearchTarget)
            target.get_current.return_value = "baseline"
            target.evaluate.side_effect = [0.8, 0.85]

            loop = ResearchLoop(target=target, experiment_dir=Path(tmpdir))
            await loop.run_experiment("Small improvement")

            tsv_path = Path(tmpdir) / "results.tsv"
            assert tsv_path.exists()
            content = tsv_path.read_text()
            assert "val_metric" in content
            assert "Small improvement" in content


class TestSubagentTargetScoring:
    """Issue #31: score from `delegate()`'s real return shapes."""

    @pytest.mark.asyncio
    async def test_evaluate_scores_success_output_and_error_result(
        self, monkeypatch, tmp_path
    ):
        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.research import SubagentTarget
        from src.sdk.tools import ToolResult

        target = SubagentTarget(tmp_path / "agent" / "PROFILE.md", eval_task="do it")

        async def succeed(self, agent_name, task, parent_id=None, timeout_seconds=None):
            return "the subagent output"  # delegate()'s real success shape

        monkeypatch.setattr(SubagentCoordinator, "delegate", succeed)
        assert await target.evaluate() == 1.0

        async def fail(self, agent_name, task, parent_id=None, timeout_seconds=None):
            return ToolResult(content="Timeout: too slow", is_error=True)  # real failure shape

        monkeypatch.setattr(SubagentCoordinator, "delegate", fail)
        assert await target.evaluate() == 0.2

    @pytest.mark.asyncio
    async def test_evaluate_does_not_swallow_programming_errors(
        self, monkeypatch, tmp_path
    ):
        from src.sdk.coordinator import SubagentCoordinator
        from src.sdk.research import SubagentTarget

        target = SubagentTarget(tmp_path / "agent" / "PROFILE.md", eval_task="do it")

        async def explode(self, agent_name, task, parent_id=None, timeout_seconds=None):
            raise RuntimeError("coordinator unavailable")

        monkeypatch.setattr(SubagentCoordinator, "delegate", explode)
        with pytest.raises(RuntimeError, match="coordinator unavailable"):
            await target.evaluate()


class TestResearchLoopEvaluatorErrors:
    @pytest.mark.asyncio
    async def test_evaluator_error_is_recorded_not_treated_as_improvement(self):
        class BrokenTarget(ResearchTarget):
            def __init__(self) -> None:
                self.rolled_back = False
                self.calls = 0

            def get_current(self) -> str:
                return "current"

            def apply_change(self, change_description: str) -> None:
                return None

            async def evaluate(self) -> float:
                self.calls += 1
                if self.calls == 1:
                    return 0.5  # the baseline evaluation succeeds
                raise RuntimeError("evaluator exploded")

            def rollback(self) -> None:
                self.rolled_back = True

        with tempfile.TemporaryDirectory() as tmpdir:
            target = BrokenTarget()
            loop = ResearchLoop(target=target, experiment_dir=Path(tmpdir))
            result = await loop.run_experiment("try something")

        assert result.status == "error"
        assert result.improved is False
        assert target.rolled_back is True
