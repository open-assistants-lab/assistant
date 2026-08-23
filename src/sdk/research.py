"""Autoresearch loop — iterative experiment infrastructure.

Follows the karpathy/autoresearch pattern:
1. Branch or snapshot current state
2. Apply a change
3. Evaluate with a fixed budget
4. If improved → keep; if worse → rollback
5. Log to results TSV
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.sdk.subagent_models import SubagentResult


@dataclass
class ExperimentResult:
    """Result of a single experiment in the research loop."""

    target_name: str
    metric_value: float
    metric_name: str = "val_metric"
    improved: bool = False
    commit_hash: str = ""
    description: str = ""
    memory_gb: float = 0.0
    status: str = "unknown"


class ResearchTarget(ABC):
    """Abstract interface for something that can be optimized by the research loop."""

    @abstractmethod
    def get_current(self) -> str:
        ...

    @abstractmethod
    def apply_change(self, change_description: str) -> None:
        ...

    @abstractmethod
    async def evaluate(self) -> float:
        ...

    @abstractmethod
    def rollback(self) -> None:
        ...


class PromptTarget(ResearchTarget):
    """Optimize a prompt text file."""

    def __init__(
        self,
        prompt_path: Path,
        eval_task: str = "",
        user_id: str = "default_user",
        workspace_id: str = "personal",
        tools: list[Any] | None = None,
    ):
        self.prompt_path = prompt_path
        self.eval_task = eval_task
        self.user_id = user_id
        self.workspace_id = workspace_id
        self._tools = tools or []
        self._backup: str | None = None

    def get_current(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    def apply_change(self, change_description: str) -> None:
        self._backup = self.get_current()
        self.prompt_path.write_text(change_description, encoding="utf-8")

    async def evaluate(self) -> float:
        try:
            from src.sdk.loop import AgentLoop, RunConfig
            from src.sdk.messages import Message
            from src.sdk.providers.factory import get_cached_model_provider

            provider = get_cached_model_provider()
            loop = AgentLoop(
                provider=provider,
                tools=self._tools,
                system_prompt=self.get_current(),
                run_config=RunConfig(max_llm_calls=3, cost_limit_usd=0.05),
                user_id=self.user_id,
                workspace_id=self.workspace_id,
            )
            messages = [Message.user(self.eval_task)]
            result = await loop.run(messages)
            last = result[-1] if result else None
            return 0.0 if last is None or last.role != "assistant" else 1.0
        except Exception:
            return 1.0

    def rollback(self) -> None:
        if self._backup is not None:
            self.prompt_path.write_text(self._backup, encoding="utf-8")
            self._backup = None


class SkillTarget(ResearchTarget):
    """Optimize a skill's SKILL.md content.

    ⚠️ STUB — evaluate() always returns 0.5 (neutral).
    Real trigger-rate evaluation is not yet implemented.
    """

    def __init__(
        self,
        skill_name: str,
        skill_path: Path,
        eval_queries: list[dict[str, Any]] | None = None,
        user_id: str = "default_user",
        workspace_id: str = "personal",
    ):
        self.skill_name = skill_name
        self.skill_path = skill_path
        self.eval_queries = eval_queries or []
        self.user_id = user_id
        self.workspace_id = workspace_id
        self._backup: str | None = None

    def get_current(self) -> str:
        return (self.skill_path / "SKILL.md").read_text(encoding="utf-8")

    def apply_change(self, change_description: str) -> None:
        self._backup = self.get_current()
        (self.skill_path / "SKILL.md").write_text(change_description, encoding="utf-8")

    async def evaluate(self) -> float:
        return 0.5

    def rollback(self) -> None:
        if self._backup is not None:
            (self.skill_path / "SKILL.md").write_text(self._backup, encoding="utf-8")
            self._backup = None


class SubagentTarget(ResearchTarget):
    """Optimize a subagent's AgentDef config."""

    def __init__(
        self,
        agent_def_path: Path,
        eval_task: str = "",
        user_id: str = "default_user",
        workspace_id: str = "personal",
    ):
        self.agent_def_path = agent_def_path
        self.eval_task = eval_task
        self.user_id = user_id
        self.workspace_id = workspace_id
        self._backup: str | None = None

    def get_current(self) -> str:
        return self.agent_def_path.read_text(encoding="utf-8")

    def apply_change(self, change_description: str) -> None:
        self._backup = self.get_current()
        self.agent_def_path.write_text(change_description, encoding="utf-8")

    async def evaluate(self) -> float:
        try:
            from src.sdk.coordinator import SubagentCoordinator

            coord = SubagentCoordinator(self.user_id, self.workspace_id)
            result: SubagentResult = await coord.delegate(self.agent_def_path.stem, self.eval_task)  # type: ignore[assignment]
            if result.success:
                return 1.0
            return 0.2
        except Exception:
            return 0.0

    def rollback(self) -> None:
        if self._backup is not None:
            self.agent_def_path.write_text(self._backup, encoding="utf-8")
            self._backup = None


@dataclass
class ResearchLoop:
    """Generic autoresearch loop: apply change → evaluate → keep or discard."""

    target: ResearchTarget
    experiment_dir: Path = Path(".")
    metric_name: str = "val_metric"
    results_file: str = "results.tsv"
    budget_seconds: int = 300
    _baseline_cache: float | None = None

    def __post_init__(self) -> None:
        self._init_results_file()

    def _init_results_file(self) -> None:
        tsv_path = self.experiment_dir / self.results_file
        if not tsv_path.exists():
            tsv_path.write_text(
                "commit\tdirty\tval_metric\tmemory_gb\tstatus\tdescription\n",
                encoding="utf-8",
            )

    def _git_available(self) -> bool:
        """Check if the root directory has a .git repo."""
        from src.storage.paths import DataPaths

        root = DataPaths().root
        return (root / ".git").exists()

    def _is_git_dirty(self) -> bool:
        try:
            import subprocess

            result = subprocess.run(
                ["git", "diff", "--quiet"], capture_output=True, timeout=5
            )
            return result.returncode != 0
        except Exception:
            return True

    def _get_commit_hash(self) -> str:
        try:
            import subprocess

            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip() or "none"
        except Exception:
            return "none"

    def _log_result(self, result: ExperimentResult) -> None:
        tsv_path = self.experiment_dir / self.results_file
        memory_gb = f"{result.memory_gb:.1f}" if result.memory_gb else "0.0"
        val = f"{result.metric_value:.6f}" if result.metric_value else "0.000000"
        dirty = "1" if self._is_git_dirty() else "0"
        line = (
            f"{result.commit_hash}\t{dirty}\t{val}\t{memory_gb}\t"
            f"{result.status}\t{result.description}\n"
        )
        with open(tsv_path, "a", encoding="utf-8") as f:
            f.write(line)

    async def run_experiment(self, change_description: str) -> ExperimentResult:
        """Run one experiment: apply change → eval → keep/discard → log."""
        target_name = (
            getattr(self.target, "skill_name", None)
            or getattr(self.target, "prompt_path", "unknown")
        )

        if self._baseline_cache is None:
            self._baseline_cache = await self.target.evaluate()
        baseline = self._baseline_cache

        self.target.apply_change(change_description)

        try:
            new_metric = await self.target.evaluate()
        except Exception:
            new_metric = 1.0

        improved = new_metric > baseline
        status = "keep" if improved else "discard"

        if not improved:
            self.target.rollback()
        else:
            self._baseline_cache = new_metric

        result = ExperimentResult(
            target_name=str(target_name),
            metric_value=new_metric,
            metric_name=self.metric_name,
            improved=improved,
            commit_hash=self._get_commit_hash(),
            description=change_description[:200],
            status=status,
        )

        self._log_result(result)
        return result

    async def run_experiments(self, changes: list[str]) -> list[ExperimentResult]:
        results = []
        for c in changes:
            results.append(await self.run_experiment(c))
        return results
