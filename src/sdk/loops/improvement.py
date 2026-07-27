"""AnalysisJob for hill-climbing (loop 4).

Reads accumulated RunOutcome records, uses an analysis LLM to identify patterns
of failure, and proposes ImprovementSuggestion objects for human review.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from src.app_logging import get_logger
from src.sdk.loops.storage import (
    ImprovementSuggestion,
    LoopEngineeringDB,
    RunOutcome,
)
from src.sdk.messages import Message

logger = get_logger()

ANALYSIS_SYSTEM_PROMPT = """You are an analysis agent. You read run outcomes from an AI assistant and propose improvements.

Each run outcome includes:
- The agent's response
- Verification status (satisfied, needs_revision, max_iterations_reached, failed, grader_error)
- Per-criterion evaluations from the grader
- Cost and token usage

Identify patterns of failure or inefficiency. Propose concrete improvements as a JSON array.

Each suggestion must have:
- target_type: "system_prompt" | "tool_description" | "rubric" | "capability" | "config"
- target_name: the specific thing to change (e.g., tool name, prompt section)
- current_value: what it currently says/does
- proposed_value: what you propose it should say/do
- rationale: why this change would help
- risk_level: "low" | "medium" | "high"

Return only valid JSON. No markdown, no code fences."""


class AnalysisJob:
    """Reads RunOutcomes and proposes ImprovementSuggestions."""

    def __init__(
        self,
        analysis_provider: Any,
        mode: str = "human_review",
        auto_apply_risk_threshold: str = "low",
        eval_suite: list[dict] | None = None,
    ) -> None:
        self._provider = analysis_provider
        self._mode = mode
        self._auto_apply_risk_threshold = auto_apply_risk_threshold
        self._eval_suite = eval_suite or []

    async def run(
        self,
        user_id: str,
        outcome_store: LoopEngineeringDB,
        suggestion_store: LoopEngineeringDB,
        since: str | None = None,
    ) -> list[ImprovementSuggestion]:
        outcomes = await outcome_store.list_run_outcomes(user_id, limit=100, since=since)
        if not outcomes:
            return []

        payload = self._build_analysis_payload(outcomes)
        messages = [
            Message.system(ANALYSIS_SYSTEM_PROMPT),
            Message.user(payload),
        ]

        try:
            response = await self._provider.chat(messages)
            content = response.content if isinstance(response.content, str) else str(response.content)
            suggestions_data = self._parse_suggestions(content)
        except Exception as exc:
            logger.error("analysis_job.failed", {"error": str(exc)}, user_id=user_id)
            return []

        suggestions: list[ImprovementSuggestion] = []
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for item in suggestions_data:
            sug = ImprovementSuggestion(
                suggestion_id=str(uuid.uuid4()),
                run_id=item.get("run_id", ""),
                target_type=item.get("target_type", ""),
                target_name=item.get("target_name", ""),
                current_value=item.get("current_value", ""),
                proposed_value=item.get("proposed_value", ""),
                rationale=item.get("rationale", ""),
                risk_level=item.get("risk_level", "low"),
                status="proposed",
                created_at=now,
            )
            await suggestion_store.save_suggestion(sug)
            suggestions.append(sug)

            if self._mode == "auto_apply" and self._risk_at_or_below(sug.risk_level, self._auto_apply_risk_threshold):
                await suggestion_store.update_suggestion_status(
                    sug.suggestion_id, "applied", now
                )
                sug.status = "applied"

        return suggestions

    def _build_analysis_payload(self, outcomes: list[RunOutcome]) -> str:
        lines = [f"Here are {len(outcomes)} recent run outcomes. Analyze them for improvement opportunities.\n"]
        for o in outcomes:
            lines.append(f"Run {o.run_id}:")
            lines.append(f"  Model: {o.model}")
            lines.append(f"  Verification: {o.verification_status} ({o.verification_iterations} iterations)")
            lines.append(f"  Cost: ${o.cost_usd:.4f} ({o.input_tokens} in / {o.output_tokens} out tokens)")
            lines.append(f"  Response: {o.response[:200]}")
            if o.verification_evaluations:
                for ev in o.verification_evaluations:
                    lines.append(f"  Grader: {ev.get('result', '?')} - {ev.get('explanation', '')[:200]}")
                    for c in ev.get("criteria", []):
                        status = "PASS" if c.get("passed") else f"FAIL: {c.get('gap', '')}"
                        lines.append(f"    [{c.get('name', '?')}] {status}")
            lines.append("")
        return "\n".join(lines)

    def _parse_suggestions(self, content: str) -> list[dict]:
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        return json.loads(content)

    def _risk_at_or_below(self, risk: str, threshold: str) -> bool:
        order = {"low": 0, "medium": 1, "high": 2}
        return order.get(risk, 3) <= order.get(threshold, 0)

    async def apply_suggestion(
        self, suggestion_id: str, store: LoopEngineeringDB
    ) -> bool:
        """Apply a suggestion. In auto-apply mode, runs eval suite first.

        TODO: Actually apply the change (edit prompt, update tool description, etc.).
        For now, just marks status as 'applied'.
        """
        import time
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if self._mode == "auto_apply" and self._eval_suite:
            eval_passed = await self._run_eval_suite()
            if not eval_passed:
                await store.update_suggestion_status(suggestion_id, "rolled_back", now)
                return False

        return await store.update_suggestion_status(suggestion_id, "applied", now)

    async def rollback_suggestion(
        self, suggestion_id: str, store: LoopEngineeringDB
    ) -> bool:
        """Rollback a previously applied suggestion.

        TODO: Actually revert the change.
        For now, just marks status as 'rolled_back'.
        """
        import time
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return await store.update_suggestion_status(suggestion_id, "rolled_back", now)

    async def _run_eval_suite(self) -> bool:
        """Run eval test cases. Returns True if all pass.

        TODO: Implement actual eval execution.
        For now, returns True (eval gate is a no-op until eval suite is implemented).
        """
        if not self._eval_suite:
            return True
        return True
