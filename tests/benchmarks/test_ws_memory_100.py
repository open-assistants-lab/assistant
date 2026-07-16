"""WebSocket Memory Integration Test — 100 interactions.

Sends 100 memory-related interactions through the WS endpoint and validates:
1. WS connection stability (no disconnects, reconnects)
2. Protocol compliance (correct message types received)
3. Memory tool invocation (agent calls message_search, message_history, etc.)
4. Memory recall accuracy (agent recalls previously stated facts)
5. Streaming correctness (text_start/delta/end, tool_input_start/end, done)
7. Error handling (no error messages, no hangs)

Usage:
    # Start server first
    uv run assistant http &
    # Run test
    uv run python tests/benchmarks/test_ws_memory_100.py
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

try:
    import websockets
except ImportError:
    print("websockets not installed. Run: uv add websockets")
    raise

WS_URL = "ws://localhost:8080/ws/conversation"
USER_ID = "ws_test_user"
TIMEOUT_PER_TURN = 60


@dataclass
class TurnResult:
    interaction_id: int
    category: str
    input_message: str
    expected_behavior: str
    got_done: bool = False
    got_error: bool = False
    got_text: bool = False
    got_tool_call: bool = False
    tool_names: list[str] = field(default_factory=list)
    response_text: str = ""
    event_types_received: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    validation_passed: bool = False
    validation_detail: str = ""


INTERACTIONS: list[dict[str, str]] = [
    # ─── Phase 1: Fact Storage (1-25) ───
    {"cat": "fact_store", "msg": "My name is Alice Chen", "expect": "acknowledge name", "validate": "alice"},
    {"cat": "fact_store", "msg": "I live in San Francisco", "expect": "acknowledge location", "validate": "san francisco"},
    {"cat": "fact_store", "msg": "I work as a software engineer at Google", "expect": "acknowledge job", "validate": "google"},
    {"cat": "fact_store", "msg": "My phone number is 555-0123", "expect": "acknowledge phone", "validate": "555"},
    {"cat": "fact_store", "msg": "I have two cats named Milo and Olive", "expect": "acknowledge pets", "validate": "milo"},
    {"cat": "fact_store", "msg": "My birthday is March 15, 1990", "expect": "acknowledge birthday", "validate": "march"},
    {"cat": "fact_store", "msg": "I speak English and Mandarin", "expect": "acknowledge languages", "validate": "mandarin"},
    {"cat": "fact_store", "msg": "My favorite color is blue", "expect": "acknowledge color", "validate": "blue"},
    {"cat": "fact_store", "msg": "I graduated from Stanford University", "expect": "acknowledge education", "validate": "stanford"},
    {"cat": "fact_store", "msg": "I drive a Tesla Model 3", "expect": "acknowledge car", "validate": "tesla"},
    {"cat": "fact_store", "msg": "My office is on the 5th floor, room 512", "expect": "acknowledge office", "validate": "512"},
    {"cat": "fact_store", "msg": "I have a meeting every Monday at 9am", "expect": "acknowledge meeting", "validate": "monday"},
    {"cat": "fact_store", "msg": "My manager is David Park", "expect": "acknowledge manager", "validate": "david"},
    {"cat": "fact_store", "msg": "I prefer dark mode for all my editors", "expect": "acknowledge preference", "validate": "dark"},
    {"cat": "fact_store", "msg": "I take the Caltrain to work", "expect": "acknowledge commute", "validate": "caltrain"},
    {"cat": "fact_store", "msg": "My lunch budget is $15 per day", "expect": "acknowledge budget", "validate": "15"},
    {"cat": "fact_store", "msg": "I have a standing desk in my office", "expect": "acknowledge desk", "validate": "standing"},
    {"cat": "fact_store", "msg": "My team uses Slack for communication", "expect": "acknowledge slack", "validate": "slack"},
    {"cat": "fact_store", "msg": "I am allergic to peanuts", "expect": "acknowledge allergy", "validate": "peanut"},
    {"cat": "fact_store", "msg": "My emergency contact is Bob Chen, 555-9999", "expect": "acknowledge contact", "validate": "bob"},
    {"cat": "fact_store", "msg": "I prefer tea over coffee", "expect": "acknowledge preference", "validate": "tea"},
    {"cat": "fact_store", "msg": "My GitHub username is alicechen", "expect": "acknowledge github", "validate": "alicechen"},
    {"cat": "fact_store", "msg": "I have a 27-inch monitor at work", "expect": "acknowledge monitor", "validate": "27"},
    {"cat": "fact_store", "msg": "My parking spot is B-23", "expect": "acknowledge parking", "validate": "b-23"},
    {"cat": "fact_store", "msg": "I use VS Code for coding", "expect": "acknowledge editor", "validate": "vs code"},

    # ─── Phase 2: Fact Recall (26-50) ───
    {"cat": "fact_recall", "msg": "What is my name?", "expect": "use memory tools", "validate": "alice"},
    {"cat": "fact_recall", "msg": "Where do I live?", "expect": "use memory tools", "validate": "san francisco"},
    {"cat": "fact_recall", "msg": "Who do I work for?", "expect": "use memory tools", "validate": "google"},
    {"cat": "fact_recall", "msg": "What are my pets' names?", "expect": "use memory tools", "validate": "milo"},
    {"cat": "fact_recall", "msg": "When is my birthday?", "expect": "use memory tools", "validate": "march"},
    {"cat": "fact_recall", "msg": "What languages do I speak?", "expect": "use memory tools", "validate": "mandarin"},
    {"cat": "fact_recall", "msg": "What is my favorite color?", "expect": "use memory tools", "validate": "blue"},
    {"cat": "fact_recall", "msg": "Where did I go to school?", "expect": "use memory tools", "validate": "stanford"},
    {"cat": "fact_recall", "msg": "What do I drive?", "expect": "use memory tools", "validate": "tesla"},
    {"cat": "fact_recall", "msg": "Who is my manager?", "expect": "use memory tools", "validate": "david"},
    {"cat": "fact_recall", "msg": "What kind of desk do I have?", "expect": "use memory tools", "validate": "standing"},
    {"cat": "fact_recall", "msg": "What am I allergic to?", "expect": "use memory tools", "validate": "peanut"},
    {"cat": "fact_recall", "msg": "Who is my emergency contact?", "expect": "use memory tools", "validate": "bob"},
    {"cat": "fact_recall", "msg": "Do I prefer tea or coffee?", "expect": "use memory tools", "validate": "tea"},
    {"cat": "fact_recall", "msg": "What is my parking spot?", "expect": "use memory tools", "validate": "b-23"},
    {"cat": "fact_recall", "msg": "What editor do I use for coding?", "expect": "use memory tools", "validate": "vs code"},
    {"cat": "fact_recall", "msg": "How do I get to work?", "expect": "use memory tools", "validate": "caltrain"},
    {"cat": "fact_recall", "msg": "What is my lunch budget?", "expect": "use memory tools", "validate": "15"},
    {"cat": "fact_recall", "msg": "What tool does my team use for communication?", "expect": "use memory tools", "validate": "slack"},
    {"cat": "fact_recall", "msg": "What is my GitHub username?", "expect": "use memory tools", "validate": "alicechen"},
    {"cat": "fact_recall", "msg": "What is my phone number?", "expect": "use memory tools", "validate": "555"},
    {"cat": "fact_recall", "msg": "What room is my office in?", "expect": "use memory tools", "validate": "512"},
    {"cat": "fact_recall", "msg": "What day is my regular meeting?", "expect": "use memory tools", "validate": "monday"},
    {"cat": "fact_recall", "msg": "What size monitor do I have?", "expect": "use memory tools", "validate": "27"},
    {"cat": "fact_recall", "msg": "What mode do I prefer for my editors?", "expect": "use memory tools", "validate": "dark"},

    # ─── Phase 3: Knowledge Update (51-65) ───
    {"cat": "knowledge_update", "msg": "I moved to New York last month", "expect": "acknowledge move", "validate": "new york"},
    {"cat": "knowledge_update", "msg": "I switched to a new team — I'm now on the Cloud Infrastructure team", "expect": "acknowledge change", "validate": "cloud"},
    {"cat": "fact_recall", "msg": "Where do I live now?", "expect": "recall updated info", "validate": "new york"},
    {"cat": "knowledge_update", "msg": "My new manager is Sarah Kim, not David Park", "expect": "acknowledge correction", "validate": "sarah"},
    {"cat": "fact_recall", "msg": "Who is my current manager?", "expect": "recall updated info", "validate": "sarah"},
    {"cat": "knowledge_update", "msg": "Actually I don't drive Tesla anymore, I switched to a Rivian", "expect": "acknowledge change", "validate": "rivian"},
    {"cat": "fact_recall", "msg": "What car do I drive now?", "expect": "recall updated info", "validate": "rivian"},
    {"cat": "knowledge_update", "msg": "I no longer prefer dark mode — I switched to light mode", "expect": "acknowledge change", "validate": "light"},
    {"cat": "fact_recall", "msg": "What mode do I prefer for editors now?", "expect": "recall updated info", "validate": "light"},
    {"cat": "knowledge_update", "msg": "I stopped drinking tea and now prefer coffee", "expect": "acknowledge change", "validate": "coffee"},
    {"cat": "fact_recall", "msg": "Do I prefer tea or coffee currently?", "expect": "recall updated info", "validate": "coffee"},
    {"cat": "knowledge_update", "msg": "My new phone number is 555-7777, the old one doesn't work", "expect": "acknowledge change", "validate": "7777"},
    {"cat": "fact_recall", "msg": "What is my current phone number?", "expect": "recall updated info", "validate": "7777"},
    {"cat": "knowledge_update", "msg": "I changed my parking spot to C-14", "expect": "acknowledge change", "validate": "c-14"},
    {"cat": "fact_recall", "msg": "What is my parking spot now?", "expect": "recall updated info", "validate": "c-14"},

    # ─── Phase 4: Search & History (66-80) ───
    {"cat": "search_history", "msg": "What have we discussed about my pets?", "expect": "use message_search", "validate": "milo"},
    {"cat": "search_history", "msg": "Can you search for conversations about my work?", "expect": "use message_search", "validate": "google"},
    {"cat": "search_history", "msg": "What was I saying about allergies?", "expect": "use message_search", "validate": "peanut"},
    {"cat": "search_history", "msg": "Search my history for anything about my commute", "expect": "use message_search", "validate": "caltrain"},
    {"cat": "search_history", "msg": "What did I tell you about my education?", "expect": "use message_search", "validate": "stanford"},
    {"cat": "search_history", "msg": "Get my conversation history from the past week", "expect": "use message_history", "validate": ""},
    {"cat": "search_history", "msg": "Show me what we talked about recently", "expect": "use message_history", "validate": ""},
    {"cat": "search_history", "msg": "Find all mentions of my team", "expect": "use message_search", "validate": "team"},
    {"cat": "search_history", "msg": "What did I say about my office setup?", "expect": "use message_search", "validate": "office"},
    {"cat": "search_history", "msg": "Search for conversations about my birthday", "expect": "use message_search", "validate": "march"},
    {"cat": "search_history", "msg": "Look up what we discussed about my diet or food preferences", "expect": "use message_search", "validate": ""},
    {"cat": "search_history", "msg": "Find anything about my GitHub account", "expect": "use message_search", "validate": "alicechen"},
    {"cat": "search_history", "msg": "What changes have I told you about recently?", "expect": "use message_search", "validate": "new york"},
    {"cat": "search_history", "msg": "Review our conversations about my workspace", "expect": "use message_search", "validate": "standing"},
    {"cat": "search_history", "msg": "What do you know about my current situation?", "expect": "synthesize updates", "validate": ""},

    # ─── Phase 5: Multi-Turn Complex (81-95) ───
    {"cat": "complex", "msg": "Summarize everything you know about me", "expect": "comprehensive recall", "validate": "alice"},
    {"cat": "complex", "msg": "What has changed about my living situation and job?", "expect": "synthesize facts", "validate": "new york"},
    {"cat": "complex", "msg": "Am I still using the same tools and editors as before?", "expect": "recall preferences", "validate": "light"},
    {"cat": "complex", "msg": "Tell me about my family situation — do I have pets?", "expect": "fact recall", "validate": "milo"},
    {"cat": "complex", "msg": "What are my current workplace details?", "expect": "synthesize", "validate": "cloud"},
    {"cat": "complex", "msg": "How do I commute now? Do I still take Caltrain?", "expect": "reason about move", "validate": "caltrain"},
    {"cat": "complex", "msg": "Create a brief profile card for me with all the key details", "expect": "comprehensive", "validate": "alice"},
    {"cat": "complex", "msg": "What are 3 things that have changed about me recently?", "expect": "reasoning", "validate": ""},
    {"cat": "complex", "msg": "If someone asks for my contact info, what should they know?", "expect": "synthesis", "validate": "555"},
    {"cat": "complex", "msg": "What preferences of mine should you always remember?", "expect": "preference recall", "validate": "coffee"},
    {"cat": "complex", "msg": "I just got a new colleague named Raj — he sits in room 515", "expect": "acknowledge new info", "validate": "raj"},
    {"cat": "complex", "msg": "What is my colleague Raj's room number?", "expect": "recall recent fact", "validate": "515"},
    {"cat": "complex", "msg": "I want to update you: I'm now learning Python and Rust", "expect": "acknowledge", "validate": "python"},
    {"cat": "complex", "msg": "What programming languages am I learning?", "expect": "recall recent fact", "validate": "rust"},
    {"cat": "complex", "msg": "Give me a final summary of everything we've discussed", "expect": "comprehensive", "validate": "alice"},

    # ─── Phase 6: Stress / Edge (96-100) ───
    {"cat": "stress", "msg": "Actually never mind about everything I just said, I was testing you", "expect": "handles gracefully", "validate": ""},
    {"cat": "stress", "msg": "What is my real name? (Not the test one)", "expect": "honest recall", "validate": "alice"},
    {"cat": "stress", "msg": "How many facts have you learned about me so far?", "expect": "uses search", "validate": ""},
    {"cat": "stress", "msg": "Can you forget my parking spot? I don't want you to remember it", "expect": "acknowledges request", "validate": ""},
    {"cat": "stress", "msg": "Goodbye! Thanks for remembering everything about me.", "expect": "graceful close", "validate": ""},
]

assert len(INTERACTIONS) == 100, f"Expected 100 interactions, got {len(INTERACTIONS)}"


async def run_single_turn(
    ws: Any,
    idx: int,
    interaction: dict[str, str],
) -> TurnResult:
    result = TurnResult(
        interaction_id=idx,
        category=interaction["cat"],
        input_message=interaction["msg"],
        expected_behavior=interaction["expect"],
    )

    msg = {
        "type": "user_message",
        "content": interaction["msg"],
        "user_id": USER_ID,
    }

    start = time.monotonic()

    try:
        await ws.send(json.dumps(msg))

        text_parts: list[str] = []
        tool_names: list[str] = []
        event_types: list[str] = []

        async for raw in ws:
            if isinstance(raw, bytes):
                raw = raw.decode()
            data = json.loads(raw)
            evt_type = data.get("type", "unknown")
            event_types.append(evt_type)

            if evt_type == "text_delta":
                text_parts.append(data.get("content", ""))
            elif evt_type == "text_start":
                pass
            elif evt_type == "text_end":
                pass
            elif evt_type == "tool_input_start":
                tool_names.append(data.get("tool", "unknown"))
            elif evt_type == "tool_result":
                tool_names.append(data.get("tool", ""))
            elif evt_type == "done":
                result.got_done = True
                break
            elif evt_type == "error":
                result.got_error = True
                result.validation_detail = data.get("message", "unknown error")
                break
            elif evt_type == "interrupt":
                approve_msg = {"type": "approve", "call_id": data.get("call_id", "")}
                await ws.send(json.dumps(approve_msg))

        result.response_text = "".join(text_parts)
        result.got_text = len(result.response_text) > 0
        result.got_tool_call = len(tool_names) > 0
        result.tool_names = tool_names
        result.event_types_received = event_types

    except Exception as e:
        result.got_error = True
        result.validation_detail = str(e)

    result.elapsed_ms = (time.monotonic() - start) * 1000

    validate_token = interaction["validate"].lower()
    if validate_token:
        response_lower = result.response_text.lower()
        if validate_token in response_lower:
            result.validation_passed = True
            result.validation_detail = f"Found '{validate_token}' in response"
        else:
            result.validation_passed = False
            result.validation_detail = f"Missing '{validate_token}' in response (got {len(result.response_text)} chars)"
    else:
        result.validation_passed = result.got_done and not result.got_error and result.got_text
        result.validation_detail = "No specific validation token — OK if no error"

    return result


async def run_all() -> list[TurnResult]:
    results: list[TurnResult] = []

    print(f"Connecting to {WS_URL} ...")
    async with websockets.connect(
        WS_URL,
        ping_interval=30,
        ping_timeout=60,
        close_timeout=30,
    ) as ws:
        print(f"Connected! Running {len(INTERACTIONS)} interactions...\n")

        for idx, interaction in enumerate(INTERACTIONS, 1):
            result = await asyncio.wait_for(
                run_single_turn(ws, idx, interaction),
                timeout=TIMEOUT_PER_TURN,
            )
            results.append(result)

            status = "OK" if result.validation_passed else "FAIL"
            tool_str = f" [{', '.join(result.tool_names)}]" if result.tool_names else ""
            err_str = f" ERR: {result.validation_detail[:60]}" if result.got_error else ""
            print(
                f"[{idx:3d}/100] {status} | {result.category:<20s} | "
                f"{result.elapsed_ms:6.0f}ms | "
                f"text={result.got_text} done={result.got_done} tools={len(result.tool_names)}"
                f"{tool_str}{err_str}"
            )

    return results


def print_report(results: list[TurnResult]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.validation_passed)
    errors = sum(1 for r in results if r.got_error)
    got_done = sum(1 for r in results if r.got_done)
    got_text = sum(1 for r in results if r.got_text)
    used_tools = sum(1 for r in results if r.got_tool_call)
    avg_ms = sum(r.elapsed_ms for r in results) / total if total else 0

    print("\n" + "=" * 80)
    print("WS MEMORY INTEGRATION TEST — 100 INTERACTIONS")
    print("=" * 80)

    print(f"\nOverall: {passed}/{total} passed ({passed/total*100:.1f}%)")
    print(f"Errors:  {errors}/{total}")
    print(f"Got 'done': {got_done}/{total}")
    print(f"Got text:   {got_text}/{total}")
    print(f"Used tools: {used_tools}/{total}")
    print(f"Avg latency: {avg_ms:.0f}ms")

    print("\n--- By Category ---")
    categories: dict[str, list[TurnResult]] = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    for cat in sorted(categories.keys()):
        cat_results = categories[cat]
        cat_passed = sum(1 for r in cat_results if r.validation_passed)
        cat_total = len(cat_results)
        cat_errors = sum(1 for r in cat_results if r.got_error)
        cat_tools = sum(1 for r in cat_results if r.got_tool_call)
        cat_avg = sum(r.elapsed_ms for r in cat_results) / cat_total if cat_total else 0
        print(
            f"  {cat:<20s}: {cat_passed}/{cat_total} passed "
            f"({cat_passed/cat_total*100:.0f}%) | "
            f"err={cat_errors} tools={cat_tools} avg={cat_avg:.0f}ms"
        )

    print("\n--- Tool Usage Stats ---")
    all_tools: dict[str, int] = {}
    for r in results:
        for t in r.tool_names:
            if t:
                all_tools[t] = all_tools.get(t, 0) + 1
    for tool, count in sorted(all_tools.items(), key=lambda x: -x[1]):
        print(f"  {tool:<30s}: {count}")

    print("\n--- Event Type Distribution ---")
    all_events: dict[str, int] = {}
    for r in results:
        for e in r.event_types_received:
            all_events[e] = all_events.get(e, 0) + 1
    for evt, count in sorted(all_events.items(), key=lambda x: -x[1]):
        print(f"  {evt:<25s}: {count}")

    print("\n--- Failures ---")
    failures = [r for r in results if not r.validation_passed]
    if not failures:
        print("  None!")
    else:
        for r in failures:
            print(
                f"  [{r.interaction_id:3d}] {r.category:<20s} | "
                f"{r.input_message[:50]:<50s} | {r.validation_detail[:60]}"
            )

    print("\n--- Summary ---")
    print(f"  Pass rate: {passed/total*100:.1f}%")
    print(f"  WS stability: {'PASS' if errors == 0 else 'FAIL'} ({errors} errors)")
    print(f"  Protocol compliance: {'PASS' if got_done == total else 'PARTIAL'} ({got_done}/{total} 'done' events)")
    print(f"  Memory tool usage: {used_tools}/{total} interactions used tools")
    print(f"  Recall accuracy: {passed}/{total} responses contained expected info")

    if passed >= 90:
        print("\n  STATUS: EXCELLENT")
    elif passed >= 75:
        print("\n  STATUS: GOOD")
    elif passed >= 50:
        print("\n  STATUS: NEEDS IMPROVEMENT")
    else:
        print("\n  STATUS: CRITICAL ISSUES")


async def main() -> None:
    results = await run_all()
    print_report(results)

    import json as json_mod
    report_path = "data/benchmarks/results/ws_memory_100_results.json"
    from pathlib import Path
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json_mod.dump(
            [
                {
                    "id": r.interaction_id,
                    "category": r.category,
                    "input": r.input_message,
                    "expected": r.expected_behavior,
                    "passed": r.validation_passed,
                    "detail": r.validation_detail,
                    "got_done": r.got_done,
                    "got_error": r.got_error,
                    "got_text": r.got_text,
                    "tools": r.tool_names,
                    "elapsed_ms": r.elapsed_ms,
                }
                for r in results
            ],
            f,
            indent=2,
        )
    print(f"\nResults saved to {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
