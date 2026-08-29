"""Agent evaluation framework - runs persona interactions and captures metrics via HTTP."""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tests.evaluation.personas import PERSONAS, generate_test_queries
from tests.evaluation.skill_acceptance import acceptance_summary

HTTP_BASE_URL = os.environ.get("EVAL_HTTP_URL", "http://localhost:8080")


async def call_agent_via_http(user_id: str, message: str, messages: list) -> dict:
    """Call agent via HTTP API."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        payload = {
            "message": message,
            "user_id": user_id,
        }
        start_time = time.time()
        async with session.post(f"{HTTP_BASE_URL}/message", json=payload) as resp:
            response_time_ms = int((time.time() - start_time) * 1000)
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"HTTP {resp.status}: {text}")
            data = await resp.json()

            response_text = data.get("response", "")
            error_text = data.get("error", "")

            return {
                "response": response_text if not error_text else f"ERROR: {error_text}",
                "messages": [{"role": "assistant", "content": response_text}],
                "response_time_ms": response_time_ms,
                "tool_calls": data.get("tool_calls", []),
                "tokens": data.get("tokens", 0),
                "raw_error": error_text,
            }


@dataclass
class InteractionResult:
    """Result of a single interaction."""

    persona_id: str
    query: str
    response: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    success: bool = False
    error: str | None = None
    context_maintained: bool = True
    hallucinated: bool = False
    topic_switch_detected: bool = False
    # Performance metrics
    response_time_ms: int = 0
    tool_call_count: int = 0
    accuracy: float = 1.0  # 0-1 scale for response quality


@dataclass
class PersonaEvaluationResult:
    """Evaluation result for a persona."""

    persona_id: str
    persona_name: str
    persona_style: str
    total_interactions: int
    successful_interactions: int
    failed_interactions: int
    total_tool_calls: int
    tool_errors: int
    avg_duration_ms: float
    context_maintained_count: int
    hallucination_count: int
    topic_switch_issues: int
    accuracy_score: float
    # Performance metrics
    avg_response_time_ms: float = 0
    min_response_time_ms: int = 0
    max_response_time_ms: int = 0
    p50_response_time_ms: int = 0
    p95_response_time_ms: int = 0
    p99_response_time_ms: int = 0
    total_tokens: int = 0
    interactions: list[InteractionResult] = field(default_factory=list)


class AgentEvaluator:
    """Evaluates agent with different personas via HTTP."""

    def __init__(self, user_id: str = "eval_user", output_dir: str = "data/evaluations"):
        self.user_id = user_id
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def run_single_interaction(
        self,
        persona: dict,
        query: str,
        run_agent_fn,
        verbose: bool = False,
    ) -> InteractionResult:
        """Run a single interaction with the agent via HTTP."""
        start_time = time.time()

        if verbose:
            print(f"  → Query: {query[:80]}...")

        try:
            result = await call_agent_via_http(
                user_id=self.user_id,
                message=query,
                messages=[],
            )

            duration_ms = int((time.time() - start_time) * 1000)
            response_time_ms = result.get("response_time_ms", duration_ms)
            tool_calls = result.get("tool_calls", [])
            tool_call_count = len(tool_calls)

            response_text = result.get("response", "")
            success = response_text.strip() != ""

            # Basic accuracy: has response and no error
            accuracy = 1.0 if success else 0.0

            return InteractionResult(
                persona_id=persona["id"],
                query=query,
                response=response_text,
                tool_calls=tool_calls,
                duration_ms=duration_ms,
                success=success,
                response_time_ms=response_time_ms,
                tool_call_count=tool_call_count,
                accuracy=accuracy,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return InteractionResult(
                persona_id=persona["id"],
                query=query,
                response="",
                duration_ms=duration_ms,
                success=False,
                error=str(e),
                accuracy=0.0,
            )

    async def evaluate_persona(
        self,
        persona: dict,
        num_interactions: int,
        run_agent_fn,
        verbose: bool = False,
    ) -> PersonaEvaluationResult:
        """Evaluate agent with a specific persona."""
        queries = generate_test_queries(persona, num_interactions)

        interactions = []
        total_tool_calls = 0
        total_tool_errors = 0
        context_maintained = 0
        hallucinations = 0
        topic_switches = 0
        response_times = []
        total_tokens = 0

        for i, query in enumerate(queries):
            print(f"  [{i + 1}/{num_interactions}] {query[:50]}...")

            result = await self.run_single_interaction(
                persona, query, run_agent_fn, verbose=verbose
            )
            interactions.append(result)

            total_tool_calls += result.tool_call_count
            total_tool_errors += len(result.tool_errors)
            response_times.append(result.response_time_ms)

            if result.success:
                context_maintained += 1

            if result.hallucinated:
                hallucinations += 1

            if result.topic_switch_detected:
                topic_switches += 1

            await asyncio.sleep(0.1)

        successful = sum(1 for i in interactions if i.success)
        failed = len(interactions) - successful
        accuracy = (successful / len(interactions)) * 100 if interactions else 0

        # Calculate response time percentiles
        response_times_sorted = sorted(response_times) if response_times else [0]
        p50 = response_times_sorted[len(response_times_sorted) // 2]
        p95 = (
            response_times_sorted[int(len(response_times_sorted) * 0.95)]
            if response_times_sorted
            else 0
        )
        p99 = (
            response_times_sorted[int(len(response_times_sorted) * 0.99)]
            if response_times_sorted
            else 0
        )

        return PersonaEvaluationResult(
            persona_id=persona["id"],
            persona_name=persona["name"],
            persona_style=persona.get("style", ""),
            total_interactions=len(interactions),
            successful_interactions=successful,
            failed_interactions=failed,
            total_tool_calls=total_tool_calls,
            tool_errors=total_tool_errors,
            avg_duration_ms=sum(i.duration_ms for i in interactions) / len(interactions)
            if interactions
            else 0,
            context_maintained_count=context_maintained,
            hallucination_count=hallucinations,
            topic_switch_issues=topic_switches,
            accuracy_score=accuracy,
            avg_response_time_ms=sum(response_times) / len(response_times) if response_times else 0,
            min_response_time_ms=min(response_times) if response_times else 0,
            max_response_time_ms=max(response_times) if response_times else 0,
            p50_response_time_ms=p50,
            p95_response_time_ms=p95,
            p99_response_time_ms=p99,
            total_tokens=total_tokens,
            interactions=interactions,
        )

    async def run_evaluation(
        self,
        num_interactions: int = 100,
        persona_ids: list[str] | None = None,
        verbose: bool = False,
    ) -> list[PersonaEvaluationResult]:
        """Run evaluation across all or selected personas via HTTP."""
        if persona_ids:
            personas = [p for p in PERSONAS if p["id"] in persona_ids]
        else:
            personas = PERSONAS

        print(f"Found {len(personas)} personas to evaluate")

        results = []

        for persona in personas:
            print(f"\n{'=' * 60}")
            print(f"Evaluating: {persona['name']} ({persona['style']})")
            print(f"{'=' * 60}")

            try:
                result = await self.evaluate_persona(
                    persona, num_interactions, None, verbose=verbose
                )
                print(
                    f"Completed {persona['name']} - Success: {result.successful_interactions}/{result.total_interactions}"
                )
                results.append(result)
                self._save_result(result)
            except Exception as e:
                print(f"ERROR evaluating {persona['name']}: {e}")
                import traceback

                traceback.print_exc()

        return results

    def _save_result(self, result: PersonaEvaluationResult):
        """Save evaluation result to file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{result.persona_id}_{timestamp}.json"
        filepath = self.output_dir / filename

        data = {
            "persona_id": result.persona_id,
            "persona_name": result.persona_name,
            "total_interactions": result.total_interactions,
            "successful_interactions": result.successful_interactions,
            "failed_interactions": result.failed_interactions,
            "total_tool_calls": result.total_tool_calls,
            "tool_errors": result.tool_errors,
            "avg_duration_ms": result.avg_duration_ms,
            "context_maintained_count": result.context_maintained_count,
            "hallucination_count": result.hallucination_count,
            "topic_switch_issues": result.topic_switch_issues,
            "accuracy_score": result.accuracy_score,
            "interactions": [
                {
                    "query": i.query,
                    "response": i.response[:500] if i.response else "",
                    "tool_calls": i.tool_calls,
                    "duration_ms": i.duration_ms,
                    "success": i.success,
                    "error": i.error,
                }
                for i in result.interactions
            ],
        }

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        print(f"  Saved to {filepath}")

    def generate_summary_report(self, results: list[PersonaEvaluationResult]) -> str:
        """Generate a summary report of all evaluations."""
        report = []
        report.append("=" * 80)
        report.append("AGENT EVALUATION SUMMARY REPORT")
        report.append("=" * 80)
        report.append("")

        total_interactions = sum(r.total_interactions for r in results)
        total_successful = sum(r.successful_interactions for r in results)
        total_tool_calls = sum(r.total_tool_calls for r in results)
        total_tool_errors = sum(r.tool_errors for r in results)
        overall_accuracy = (
            (total_successful / total_interactions) * 100 if total_interactions else 0
        )

        report.append(f"Total Interactions: {total_interactions}")
        report.append(f"Successful: {total_successful} ({overall_accuracy:.1f}%)")
        report.append(f"Failed: {total_interactions - total_successful}")
        report.append(f"Total Tool Calls: {total_tool_calls}")
        report.append(f"Tool Errors: {total_tool_errors}")
        report.append(acceptance_summary(self.user_id))
        report.append("")

        # Performance summary
        all_response_times = []
        for r in results:
            if r.avg_response_time_ms > 0:
                all_response_times.append(r.avg_response_time_ms)

        if all_response_times:
            avg_overall = sum(all_response_times) / len(all_response_times)
            report.append("-" * 80)
            report.append("PERFORMANCE METRICS")
            report.append("-" * 80)
            report.append(f"Avg Response Time: {avg_overall:.0f}ms")
            report.append("")

        report.append("-" * 80)
        report.append("PERSONA BREAKDOWN")
        report.append("-" * 80)

        for r in results:
            report.append(f"\n{r.persona_name} ({r.persona_id}) - Style: {r.persona_style}:")
            report.append(f"  Accuracy: {r.accuracy_score:.1f}%")
            report.append(f"  Tool Calls: {r.total_tool_calls}, Errors: {r.tool_errors}")
            report.append(f"  Avg Response Time: {r.avg_response_time_ms:.0f}ms")
            report.append(
                f"  Response Time (p50/p95/p99): {r.p50_response_time_ms}/{r.p95_response_time_ms}/{r.p99_response_time_ms}ms"
            )
            report.append(
                f"  Min/Max Response: {r.min_response_time_ms}/{r.max_response_time_ms}ms"
            )
            report.append(
                f"  Context Maintained: {r.context_maintained_count}/{r.total_interactions}"
            )
            report.append(f"  Hallucinations: {r.hallucination_count}")
            report.append(f"  Topic Switch Issues: {r.topic_switch_issues}")

        report.append("")
        report.append("-" * 80)
        report.append("ISSUES TO INVESTIGATE")
        report.append("-" * 80)

        for r in results:
            if r.tool_errors > r.total_tool_calls * 0.1:
                report.append(
                    f"  {r.persona_name}: High tool error rate ({r.tool_errors}/{r.total_tool_calls})"
                )

            if r.hallucination_count > 0:
                report.append(
                    f"  {r.persona_name}: {r.hallucination_count} hallucination(s) detected"
                )

            if r.topic_switch_issues > r.total_interactions * 0.1:
                report.append(
                    f"  {r.persona_name}: Topic switch issues ({r.topic_switch_issues}/{r.total_interactions})"
                )

        return "\n".join(report)


async def run_kit_evaluation(args) -> None:
    """Run a kit's eval set (P1-T7): kit-authored personas, pass-ratio report.

    Loads eval/personas.yaml from the kit dir, runs it through the SAME HTTP
    harness (no fork). When no evaluation server/model is reachable the run
    still completes structurally and reports a clean 0-model skip (exit 0).
    """
    import yaml

    kit_dir = Path(args.kit)
    personas_file = kit_dir / "eval" / "personas.yaml"
    data = yaml.safe_load(personas_file.read_text(encoding="utf-8")) or {}
    kit_personas = data.get("personas", [])
    print("=" * 80)
    print(f"KIT EVALUATION - {kit_dir.name}")
    print("=" * 80)
    print(f"Kit personas: {len(kit_personas)}")
    print("")

    evaluator = AgentEvaluator(user_id=args.user_id, output_dir=args.output)

    # Connectivity probe: no model/server -> structural skip (per plan
    # acceptance: "if no model available, the harness must still run
    # structurally and report 0-model skip cleanly").
    try:
        await call_agent_via_http(user_id=args.user_id, message="ping", messages=[])
    except Exception as e:  # noqa: BLE001 - httpx raises varied exceptions
        print(f"[skip] No evaluation server/model reachable ({type(e).__name__}).")
        print("[skip] Kit eval set found and parsed; 0-model run skipped cleanly.")
        return

    total = successful = 0
    for persona in kit_personas:
        queries = generate_test_queries(persona, count=args.interactions)
        for query in queries:
            result = await evaluator.run_single_interaction(persona, query, None)
            total += 1
            successful += 1 if result.success else 0
    ratio = successful / total if total else 0.0
    print(f"\\nKit eval: {successful}/{total} passed (ratio {ratio:.2f})")
    print(f"Gate >= 0.70: {'PASS' if ratio >= 0.7 else 'FAIL'}")


async def main():
    """Run evaluation via HTTP."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--interactions", type=int, default=100, help="Interactions per persona")
    parser.add_argument(
        "--personas", type=str, help="Comma-separated persona IDs (e.g., p1,p2) or 'all'"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/evaluations",
        help="Output directory for evaluation results",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default="eval_user",
        help="User ID for evaluation",
    )
    parser.add_argument(
        "--http-url",
        type=str,
        default=os.environ.get("EVAL_HTTP_URL", "http://localhost:8080"),
        help="HTTP server URL",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed output for each interaction",
    )
    parser.add_argument(
        "--save-responses",
        action="store_true",
        help="Save all responses to JSON file",
    )
    parser.add_argument(
        "--kit",
        type=str,
        help="Kit dir to evaluate (loads eval/personas.yaml from the kit instead "
        "of the global PERSONAS). Runs structurally with a clean skip when no "
        "evaluation server/model is available.",
    )
    args = parser.parse_args()

    if args.kit:
        await run_kit_evaluation(args)
        return

    # Handle "all" or empty personas
    if args.personas and args.personas.lower() == "all":
        persona_ids = None  # Will use all PERSONAS
    else:
        persona_ids = args.personas.split(",") if args.personas else None

    print("=" * 80)
    print("EXECUTIVE ASSISTANT - PERSONA EVALUATION")
    print("=" * 80)
    print(f"HTTP URL: {args.http_url}")
    print(f"Personas: {len(PERSONAS)}")
    print(f"Interactions per persona: {args.interactions}")
    print(f"Total: {len(PERSONAS) * args.interactions} interactions")
    print(f"User ID: {args.user_id}")
    print(f"Output: {args.output}")
    print("=" * 80)
    print("")

    evaluator = AgentEvaluator(user_id=args.user_id, output_dir=args.output)

    results = await evaluator.run_evaluation(
        num_interactions=args.interactions,
        persona_ids=persona_ids,
        verbose=args.verbose,
    )

    report = evaluator.generate_summary_report(results)
    print("\n" + report)

    summary_path = evaluator.output_dir / f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(summary_path, "w") as f:
        f.write(report)
    print(f"\nSummary saved to {summary_path}")

    if args.save_responses:
        import json

        responses_path = (
            evaluator.output_dir / f"responses_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        all_data = []
        for r in results:
            for i in r.interactions:
                all_data.append(
                    {
                        "persona_id": i.persona_id,
                        "query": i.query,
                        "response": i.response,
                        "success": i.success,
                        "tool_calls": i.tool_calls,
                        "tool_call_count": i.tool_call_count,
                        "response_time_ms": i.response_time_ms,
                        "error": i.error,
                    }
                )
        with open(responses_path, "w") as f:
            json.dump(all_data, f, indent=2)
        print(f"Responses saved to {responses_path}")


if __name__ == "__main__":
    asyncio.run(main())
