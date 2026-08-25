"""Memory system performance benchmark.

Two modes:
  --component   Direct calls to memory store/middleware (no server needed)
  --ws          WebSocket end-to-end test (requires server on port 8080)

Seeds realistic data before running. Reports p50/p95/p99 latencies per component.

Usage:
  uv run python tests/perf/test_memory_pipeline.py --component --runs 10
  uv run python tests/perf/test_memory_pipeline.py --ws --runs 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

TEST_USER = "perf_test_user"
TEST_WORKSPACE = "personal"

SAMPLE_MEMORIES = [
    ("user's name", "Jordan Mitchell", "personal", "fact"),
    ("user's job title", "Senior Backend Engineer", "work", "fact"),
    ("user's company", "Acme AI", "work", "fact"),
    ("user's location", "San Francisco, CA", "location", "fact"),
    ("user's manager", "Sarah Chen", "work", "fact"),
    ("user's team size", "7 engineers", "work", "fact"),
    ("user's preferred language", "Python", "skills", "preference"),
    ("user's preferred editor", "VS Code with Vim keybindings", "tools", "preference"),
    ("user's coffee preference", "Iced oat milk latte", "personal", "preference"),
    ("user's pet", "Golden retriever named Biscuit", "personal", "fact"),
    ("user's commute", "Bike across Golden Gate Bridge", "location", "fact"),
    ("user's working hours", "8am to 4pm PT", "workflow", "workflow"),
    ("user's meeting style", "Prefers async over sync meetings", "communication", "preference"),
    ("user's tech stack", "Python, FastAPI, PostgreSQL, Redis", "skills", "fact"),
    ("user's project", "Building real-time analytics dashboard", "work", "fact"),
    ("user's allergies", "Shellfish allergy", "personal", "fact"),
    ("user's vacation preference", "Beach over mountains", "interests", "preference"),
    ("user's favorite book", "Project Hail Mary by Andy Weir", "interests", "preference"),
    ("user's side project", "Open source MCP server for Notion", "work", "fact"),
    ("user's correction", "moved to Denver from SF in January 2026", "location", "correction"),
]

SAMPLE_CONVERSATION = [
    ("user", "Hey, can you help me set up a new microservice for the analytics pipeline?"),
    ("assistant", "Of course! What stack are you thinking? You typically use Python and FastAPI."),
    ("user", "Yes, Python and FastAPI. I prefer PostgreSQL for this one."),
    ("assistant", "Makes sense given your team's expertise with Postgres."),
    ("user", "Please do. By the way, my new manager is Tom now, not Sarah."),
    ("assistant", "Noted — your manager is now Tom. I'll scaffold the project."),
    ("user", "I like the standard FastAPI layout with separate routers and services directories."),
    ("assistant", "Got it. Standard FastAPI layout with routers/ and services/."),
    ("user", "Also, I should mention I moved to Denver in January."),
    ("assistant", "Thanks for letting me know! Denver is great."),
    ("user", "Thanks. What do you remember about my tech preferences?"),
    ("assistant", "You work with Python and FastAPI, prefer PostgreSQL, use VS Code."),
    ("user", "Perfect. Also my coffee order changed — I'm doing hot lattes now."),
    ("assistant", "Updated — hot lattes it is!"),
    ("user", "Nope, that's it for now. Thanks!"),
    ("assistant", "Happy to help, Jordan!"),
]


@contextmanager
def timed(label: str, times_list: list[float] | None = None):
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = (time.perf_counter() - start) * 1000
        if times_list is not None:
            times_list.append(elapsed)


def report_stats(name: str, values: list[float]) -> dict:
    if not values:
        return {"name": name, "count": 0, "p50": 0, "p95": 0, "p99": 0, "mean": 0, "min": 0, "max": 0}
    s = sorted(values)
    return {
        "name": name,
        "count": len(values),
        "p50": statistics.median(s),
        "p95": s[int(len(s) * 0.95)],
        "p99": s[int(len(s) * 0.99)],
        "mean": statistics.mean(s),
        "min": min(s),
        "max": max(s),
    }


def seed_data() -> None:
    from src.storage.messages import get_message_store

    store = get_message_store(TEST_USER, TEST_WORKSPACE)
    core = store.core

    existing = core.get_observations(limit=1)
    if existing:
        core._db.raw_query("DELETE FROM observations")
        core._db.raw_query("DELETE FROM reflections")

    for role, content in SAMPLE_CONVERSATION:
        store.add_message(role, content)

    print(f"Seeded {len(SAMPLE_CONVERSATION)} messages")


def run_component_benchmarks(num_runs: int) -> dict:
    from src.storage.messages import get_message_store

    store = get_message_store(TEST_USER, TEST_WORKSPACE)
    core = store.core
    results: dict[str, Any] = {}

    # ── 1. Embedding cost (most critical) ──
    print("  [1/7] Embedding generation (all-MiniLM-L6-v2, 384-dim)...")
    embed_times: list[float] = []
    for _ in range(5):
        core.db._get_embedding("warmup text")
    for i in range(20):
        with timed(f"embed_run_{i}", embed_times):
            core.db._get_embedding("What is my current job title and company name")
    results["embedding"] = report_stats("get_embedding (all-MiniLM-L6-v2, 384d)", embed_times)

    # ── 2. search_hybrid (ChromaDB + FTS5 + RRF) on messages ──
    print("  [2/7] search_hybrid (ChromaDB vector + FTS5 + RRF) — MOST EXPENSIVE...")
    hybrid_times: list[float] = []
    hybrid_queries = [
        "What programming languages do I use",
        "Tell me about my work setup",
        "What do I like for coffee",
    ] * (num_runs // 3 + 1)
    for q in hybrid_queries[:num_runs]:
        with timed("hybrid", hybrid_times):
            core.search_enhanced(q, limit=8)
    results["search_hybrid"] = report_stats("search_hybrid (ChromaDB+FTS5+RRF)", hybrid_times)

    # ── 3. search_observations (hybrid) ──
    print("  [3/7] search_observations (hybrid)...")
    obs_times: list[float] = []
    obs_queries = [
        "What is my job title",
        "Where do I live",
        "What's my tech stack",
    ] * (num_runs // 3 + 1)
    for q in obs_queries[:num_runs]:
        with timed("obs", obs_times):
            core.search_observations(q, limit=6)
    results["search_observations"] = report_stats("search_observations (hybrid)", obs_times)

    # ── 4. get_observations (recent) ──
    print("  [4/7] get_observations (recent)...")
    recent_times: list[float] = []
    for _ in range(num_runs):
        with timed("recent", recent_times):
            core.get_observations(limit=20)
    results["get_observations"] = report_stats("get_observations (recent 20)", recent_times)

    # ── 5. Full retrieval pipeline ──
    print("  [5/7] Full retrieval pipeline...")
    pipeline_times: list[float] = []
    pipeline_queries = [
        "What do you remember about my job and preferences?",
        "Search my memory for anything about my work",
        "What's my current location and manager?",
    ] * (num_runs // 3 + 1)
    for q in pipeline_queries[:num_runs]:
        start = time.perf_counter()
        core.search_observations(q, limit=6)
        core.search_enhanced(q, limit=8)
        pipeline_times.append((time.perf_counter() - start) * 1000)
    results["retrieval_pipeline"] = report_stats("Full retrieval pipeline (2 DB queries)", pipeline_times)

    # ── 6. insert_observations (write path) ──
    print("  [6/7] insert_observations (write path)...")
    upsert_times: list[float] = []
    upsert_obs = [
        {"content": "User likes Sushi", "kind": "fact", "user_id": TEST_USER},
        {"content": "User is in MST timezone", "kind": "fact", "user_id": TEST_USER},
        {"content": "User has AWS Solutions Architect cert", "kind": "fact", "user_id": TEST_USER},
        {"content": "User parks at Level 3, Section B", "kind": "fact", "user_id": TEST_USER},
        {"content": "User has iPhone 16 Pro", "kind": "fact", "user_id": TEST_USER},
    ]
    for obs in upsert_obs:
        with timed("upsert", upsert_times):
            core.insert_observations([obs])
    results["insert_observations"] = report_stats("insert_observations (write)", upsert_times)

    # ── 7. journal processing cost ──
    print("  [7/7] What-if: cost breakdown (embedding = N × HybridDB search)...")
    results["_cost_model"] = {
        "embedding_per_call_ms": statistics.mean(embed_times),
        "hybrid_search_per_call_ms": statistics.mean(hybrid_times),
        "pipeline_per_call_ms": statistics.mean(pipeline_times),
        "embedding_fraction_of_hybrid": (
            statistics.mean(embed_times) / statistics.mean(hybrid_times) * 100
            if statistics.mean(hybrid_times) > 0 else 0
        ),
    }

    return results


async def run_ws_benchmark(num_runs: int) -> dict:
    ws_url = os.environ.get("WS_URL", "ws://localhost:8080/ws/conversation")
    results: dict[str, Any] = {}

    async def send_and_wait(ws: Any, message: str) -> float:
        start = time.perf_counter()
        await ws.send(json.dumps({
            "type": "user_message",
            "content": message,
            "user_id": TEST_USER,
        }))
        while True:
            raw = await ws.recv()
            data = json.loads(raw)
            if data.get("type") == "done":
                return (time.perf_counter() - start) * 1000

    print(f"Connecting to {ws_url}")
    print("Make sure 'uv run assistant http' is running on port 8080")

    try:
        import websockets
    except ImportError:
        print("websockets not installed. Install with: uv pip install websockets")
        return results

    try:
        async with websockets.connect(ws_url) as ws:
            print("  Connected.")

            scenarios = [
                ("noop_hello", "Hello!", "No-op greeting"),
                ("memory_injected", "What do you remember about my job title and location?", "Context injection"),
                ("message_search", "Search my memory for everything about my preferences", "message_search tool"),
                ("message_history", "What did we discuss in the past 2 weeks?", "message_history tool"),
            ]

            for sid, msg, desc in scenarios:
                print(f"  [{sid}] {desc}...")
                times = []
                for i in range(min(num_runs, 5)):
                    try:
                        elapsed = await send_and_wait(ws, msg)
                        times.append(elapsed)
                        print(f"    Run {i+1}: {elapsed:.0f}ms")
                    except Exception as e:
                        print(f"    Run {i+1}: FAILED ({e})")
                    await asyncio.sleep(0.5)
                results[sid] = report_stats(desc, times)
    except Exception as e:
        print(f"WebSocket connection failed: {e}")
        print("Make sure the server is running on port 8080")

    return results


def print_results(results: dict, mode: str) -> None:
    print(f"\n{'='*80}")
    print(f"  Memory Pipeline Performance Report ({mode})")
    print(f"{'='*80}\n")
    print(f"{'Component':<50} {'p50':>8} {'p95':>8} {'p99':>8} {'mean':>9} {'n':>5}")
    print("-" * 90)

    for key, stats in sorted(results.items()):
        if key.startswith("_"):
            continue
        if isinstance(stats, dict) and "p50" in stats:
            print(
                f"{stats['name']:<50} "
                f"{stats['p50']:8.1f} "
                f"{stats['p95']:8.1f} "
                f"{stats['p99']:8.1f} "
                f"{stats['mean']:9.1f} "
                f"{stats['count']:5d}"
            )

    cost = results.get("_cost_model", {})
    if cost:
        print(f"\n{'─'*90}")
        print("  Cost Model:")
        print(f"    Embedding: {cost['embedding_per_call_ms']:.1f}ms/call")
        print(f"    Hybrid search: {cost['hybrid_search_per_call_ms']:.1f}ms/call")
        print(f"    Retrieval pipeline: {cost['pipeline_per_call_ms']:.1f}ms/call")
        print(f"    Embedding as % of hybrid search: {cost['embedding_fraction_of_hybrid']:.1f}%")
        print(f"    Embeddings/day (100 queries): {cost['embedding_per_call_ms'] * 100:.0f}ms total")


def main() -> None:
    parser = argparse.ArgumentParser(description="Memory pipeline performance benchmark")
    parser.add_argument("--component", action="store_true", help="Run component-level benchmarks")
    parser.add_argument("--ws", action="store_true", help="Run WebSocket end-to-end benchmarks")
    parser.add_argument("--runs", type=int, default=10, help="Number of runs per scenario (default: 10)")
    parser.add_argument("--output", type=str, default="", help="Output JSON file path")
    parser.add_argument("--json", type=str, default="", help="Alias for --output")
    args = parser.parse_args()

    num_runs = args.runs
    output_path = args.output or args.json
    all_results: dict[str, Any] = {}

    if args.component:
        print("Seeding test data...")
        seed_data()
        print("\nRunning component benchmarks...")
        component_results = run_component_benchmarks(num_runs)
        all_results["component"] = component_results
        print_results(component_results, "component")

    if args.ws:
        print("\nRunning WebSocket benchmarks...")
        ws_results = asyncio.run(run_ws_benchmark(num_runs))
        all_results["ws"] = ws_results
        print_results(ws_results, "websocket")

    if not args.component and not args.ws:
        parser.print_help()
        sys.exit(1)

    if output_path:
        output = {}
        for mode, results in all_results.items():
            output[mode] = {
                k: v for k, v in results.items()
                if not k.startswith("_") and isinstance(v, dict) and "p50" in v
            }
        Path(output_path).write_text(json.dumps(output, indent=2))
        print(f"\nResults written to {output_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
