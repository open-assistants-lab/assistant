"""Research tools — start, list, and manage autoresearch experiments.

These tools enable the agent to run iterative self-improvement experiments
on prompts, skills, and subagent configs.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

from src.sdk.research import ExperimentResult, ResearchLoop
from src.sdk.tools import ToolAnnotations, ToolDefinition
from src.storage.paths import DEFAULT_USER_ID

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            thread = threading.Thread(target=_loop.run_forever, daemon=True)
            thread.start()
        return _loop


def _run_async(coro: Any) -> Any:
    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=300)


def _research_start(
    target_type: str,
    target_ref: str,
    change_description: str,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
    budget_seconds: int = 300,
) -> str:
    """Start a research experiment on a prompt, skill, or subagent.

    Applies a change, evaluates it, then keeps or discards based on the metric.

    Args:
        target_type: Type of target — "prompt", "skill", or "subagent"
        target_ref: Path to the target file or skill name
        change_description: The change to apply and evaluate
        user_id: User identifier
        workspace_id: Workspace identifier
        budget_seconds: Max seconds for the experiment

    Returns:
        Human-readable summary of the experiment result.
    """
    target_path = Path(target_ref)

    target: Any
    if target_type == "prompt":
        from src.sdk.research import PromptTarget

        target = PromptTarget(target_path, user_id=user_id, workspace_id=workspace_id)
    elif target_type == "skill":
        from src.sdk.research import SkillTarget

        target = SkillTarget(
            skill_name=target_path.stem,
            skill_path=target_path,
            user_id=user_id,
            workspace_id=workspace_id,
        )
    elif target_type == "subagent":
        from src.sdk.research import SubagentTarget

        target = SubagentTarget(target_path, user_id=user_id, workspace_id=workspace_id)
    else:
        return f"Unknown target_type: {target_type}"

    loop = ResearchLoop(
        target=target,
        experiment_dir=target_path.parent if target_path.is_dir() else target_path.parent,
        budget_seconds=budget_seconds,
    )

    result = _run_async(loop.run_experiment(change_description))
    return _format_result(result)


research_start = ToolDefinition(
    name="research_start",
    description=_research_start.__doc__ or "",
    parameters={
        "type": "object",
        "properties": {
            "target_type": {
                "type": "string",
                "enum": ["prompt", "skill", "subagent"],
                "description": "Type of target to optimize",
            },
            "target_ref": {
                "type": "string",
                "description": "Path to target file or skill name",
            },
            "change_description": {
                "type": "string",
                "description": "The change to apply and evaluate",
            },
            "user_id": {"type": "string", "default": DEFAULT_USER_ID},
            "workspace_id": {"type": "string", "default": "personal"},
            "budget_seconds": {"type": "integer", "default": 300},
        },
        "required": ["target_type", "target_ref", "change_description"],
    },
    annotations=ToolAnnotations(
        read_only=True,
        title="Research: Start Experiment",
    ),
    function=_research_start,
)


def _research_list(
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
) -> str:
    """List past research experiments from the results TSV.

    Scans known experiment directories for results.tsv files.

    Args:
        user_id: User identifier
        workspace_id: Workspace identifier

    Returns:
        A formatted list of past experiments.
    """
    from src.storage.paths import DataPaths

    base = DataPaths(user_id=user_id, workspace_id=workspace_id).research_dir()
    if not base.exists():
        return "No research experiments found."

    lines = []
    for tsv in sorted(base.rglob("results.tsv")):
        project = tsv.parent.name
        content = tsv.read_text(encoding="utf-8").strip().split("\n")
        if len(content) <= 1:
            continue
        lines.append(f"\n[{project}]")
        for row in content[1:]:
            parts = row.split("\t")
            if len(parts) >= 6:
                commit, dirty, val, mem, status, desc = parts[:6]
                lines.append(
                    f"  {commit} | {status:8s} | val={val} | {desc[:60]}"
                )
    return "\n".join(lines) if lines else "No research experiments found."


research_list = ToolDefinition(
    name="research_list",
    description=_research_list.__doc__ or "",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "default": DEFAULT_USER_ID},
            "workspace_id": {"type": "string", "default": "personal"},
        },
    },
    annotations=ToolAnnotations(
        read_only=True,
        title="Research: List Experiments",
    ),
    function=_research_list,
)


def _format_result(result: ExperimentResult) -> str:
    verb = "improved" if result.improved else "unchanged/worse"
    return (
        f"Experiment: {result.description[:80]}\n"
        f"  Status:   {result.status} ({verb})\n"
        f"  Metric:   {result.metric_name}={result.metric_value:.4f}\n"
        f"  Commit:   {result.commit_hash}\n"
        f"  Target:   {result.target_name}"
    )
