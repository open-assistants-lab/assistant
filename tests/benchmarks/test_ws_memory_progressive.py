"""Progressive WS Memory Test — 100, 200, 300, 400, 500 turns.

Runs the same interaction set but progressively, reporting accuracy at each
milestone to measure degradation over conversation length.

Usage:
    uv run assistant http &
    uv run python tests/benchmarks/test_ws_memory_progressive.py
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import websockets
except ImportError:
    print("websockets not installed. Run: uv add websockets")
    raise


WS_URL = "ws://localhost:8080/ws/conversation"
USER_ID = "ws_progressive_user"
TIMEOUT_PER_TURN = 90


@dataclass
class TurnResult:
    interaction_id: int
    category: str
    input_message: str
    expected_behavior: str
    validate_token: str
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


def build_interactions() -> list[dict[str, str]]:
    interactions: list[dict[str, str]] = []
    # Phase 1: Store facts (1-25)
    store_facts = [
        ("My name is Jordan Mitchell", "jordan"),
        ("I live in Austin, Texas", "austin"),
        ("I work as a data analyst at Dell Technologies", "dell"),
        ("My wife's name is Priya, she's a physician", "priya"),
        ("I have a golden retriever named Biscuit", "biscuit"),
        ("I'm allergic to shellfish", "shellfish"),
        ("My birthday is June 22", "june"),
        ("I prefer Python for data analysis", "python"),
        ("My favorite lunch spot is Torchy's Tacos", "torchy"),
        ("I take the MetroRail to work", "metrorail"),
        ("My manager is Karen Walsh", "karen"),
        ("I drive a 2021 Honda Civic", "civic"),
        ("My desk is in Building 4, Floor 2", "building 4"),
        ("I prefer dark roast coffee", "dark roast"),
        ("My email is jordan.mitchell@dell.com", "jordan.mitchell"),
        ("I have a 34-inch ultrawide monitor at work", "ultrawide"),
        ("My regular work hours are 8:30 AM to 5:30 PM", "8:30"),
        ("I'm learning Rust on weekends", "rust"),
        ("I prefer using Jira for project tracking", "jira"),
        ("My current project is the Customer Churn Prediction dashboard", "churn"),
        ("I work from home on Fridays", "friday"),
        ("My parking spot at Dell garage is G-12", "g-12"),
        ("I have a gym membership at Lifetime Fitness", "lifetime"),
        ("My best friend is Tyler, he works at Samsung", "tyler"),
        ("I read mostly sci-fi, favorite author is Adrian Tchaikovsky", "tchaikovsky"),
    ]
    for msg, v in store_facts:
        interactions.append({"cat": "fact_store", "msg": msg, "expect": "store", "validate": v})

    # Phase 2: Recall (26-75)
    recall_facts = [
        ("What is my name?", "jordan"), ("Where do I live?", "austin"),
        ("Who do I work for?", "dell"), ("What is my wife's name?", "priya"),
        ("What is my dog's name?", "biscuit"), ("Am I allergic to anything?", "shellfish"),
        ("When is my birthday?", "june"), ("What programming language do I prefer?", "python"),
        ("What is my favorite lunch spot?", "torchy"), ("How do I commute to work?", "metrorail"),
        ("Who is my manager?", "karen"), ("What car do I drive?", "civic"),
        ("Where is my desk?", "building 4"), ("What kind of coffee do I drink?", "dark"),
        ("What is my email address?", "jordan.mitchell"), ("What size monitor do I have?", "34"),
        ("What are my work hours?", "8:30"), ("What language am I learning?", "rust"),
        ("What project tracker do I prefer?", "jira"), ("What is my current project?", "churn"),
        ("Do I work from home? When?", "friday"), ("What is my parking spot?", "g-12"),
        ("Where do I go to the gym?", "lifetime"), ("Who is my best friend?", "tyler"),
        ("What genre of books do I read?", "sci-fi"),
        ("What author do I like?", "tchaikovsky"),
        ("What is my dog's breed?", "golden"), ("What city do I live in?", "austin"),
        ("What state do I live in?", "texas"), ("What is my role at Dell?", "data analyst"),
        ("Where does Priya work?", "physician"), ("What is Biscuit's name?", "biscuit"),
        ("What am I allergic to?", "shellfish"), ("What month is my birthday?", "june"),
        ("What is my preferred language?", "python"),
        ("What is my lunch restaurant?", "torchy"),
        ("What transit do I take?", "metrorail"),
        ("What is my manager's last name?", "walsh"),
        ("What year is my car?", "2021"),
        ("What floor is my desk on?", "floor 2"),
        ("How do I take my coffee?", "dark"),
        ("What is my Dell email domain?", "dell"),
        ("What kind of monitor do I have?", "ultrawide"),
        ("When do I start work?", "8:30"),
        ("What do I learn on weekends?", "rust"),
        ("What tool do I use for tracking projects?", "jira"),
        ("What dashboard am I working on?", "churn"),
        ("Which day do I WFH?", "friday"),
        ("Where do I park at work?", "g-12"),
    ]
    for msg, v in recall_facts:
        interactions.append({"cat": "fact_recall", "msg": msg, "expect": "recall", "validate": v})

    # Phase 3: Updates (76-100)
    updates = [
        ("I moved to Denver last week", "denver"),
        ("My new manager is Tom Bradley. Karen Walsh retired", "tom"),
        ("I switched from coffee to green tea", "green tea"),
        ("I no longer use Jira. I switched to Linear", "linear"),
        ("My parking spot changed to H-09", "h-09"),
        ("I started biking to work instead of the MetroRail", "bik"),
        ("Where do I live now?", "denver"),
        ("Who is my current manager?", "tom"),
        ("Do I drink coffee or tea?", "tea"),
        ("What project tracker do I use now?", "linear"),
        ("What is my parking spot?", "h-09"),
        ("How do I commute now?", "bik"),
        ("I switched to Neovim from VS Code", "neovim"),
        ("I got a promotion to Senior Data Analyst", "senior"),
        ("I stopped going to Lifetime Fitness", "cancel"),
        ("What editor do I use now?", "neovim"),
        ("What is my job title?", "senior"),
        ("What happened to my gym membership?", "cancel"),
        ("I prefer to be called JD by friends", "jd"),
        ("I changed my phone number to 512-555-8899", "8899"),
        ("What nickname do I prefer?", "jd"),
        ("What is my phone number now?", "8899"),
        ("My project changed to Real-Time Analytics Pipeline", "pipeline"),
        ("What is my current project?", "pipeline"),
        ("I no longer take the MetroRail, I bike everywhere", "bik"),
    ]
    for msg, v in updates:
        interactions.append({"cat": "knowledge_update", "msg": msg, "expect": "update/recall", "validate": v})

    # Phase 4: Search & Complex (101-250)
    search_qs = [
        ("What have we discussed about my commute?", "bik"),
        ("Search for conversations about my pets", "biscuit"),
        ("What did I tell you about my job changes?", "senior"),
        ("Find messages about my housing situation", "denver"),
        ("Look up my coffee and tea preferences", "tea"),
        ("What is my full profile summary?", "jordan"),
        ("Give me a timeline of my career at Dell", "dell"),
        ("What are all the changes I've made recently?", "denver"),
        ("Summarize everything you know about me", "jordan"),
        ("What has changed about my commute?", "bik"),
        ("Compare my original preferences vs current", "dark"),
        ("What are my current contact details?", "8899"),
        ("What editor do I use now vs before?", "neovim"),
        ("Who were my managers at Dell?", "karen"),
        ("What project did I work on before Pipeline?", "churn"),
        ("Find discussions about my transportation", "bik"),
        ("What do I drink now?", "tea"),
        ("Search for my work details", "dell"),
        ("What is my current parking spot?", "h-09"),
        ("What tool do I use for tracking projects?", "linear"),
        ("What happened to my metro commute?", "metrorail"),
        ("Where do I live?", "denver"),
        ("Am I still using Jira?", "linear"),
        ("Do I still go to the gym?", "cancel"),
        ("What is my nickname preference?", "jd"),
    ]
    for msg, v in search_qs:
        interactions.append({"cat": "search", "msg": msg, "expect": "search/complex", "validate": v})

    # Phase 5: Repeat recalls to test idempotency (251-350)
    repeat_qs = [
        ("What is my name again?", "jordan"),
        ("Where do I live?", "denver"),
        ("What is my wife's name?", "priya"),
        ("Where do I work?", "dell"),
        ("Do I have pets?", "biscuit"),
        ("What is my current project?", "pipeline"),
    ]
    for _ in range(17):
        for msg, v in repeat_qs:
            interactions.append({"cat": "idempotent", "msg": msg, "expect": "repeat recall", "validate": v})
    # Trim to exact count
    excess = len(interactions) - 350
    if excess > 0:
        interactions = interactions[:350]

    # Phase 6: More updates and recalls (351-450)
    more_updates = [
        ("I moved from Denver to Chicago", "chicago"),
        ("I switched from green tea to oat milk lattes", "oat milk"),
        ("My project changed to the Data Platform team", "data platform"),
        ("I sold my Honda Civic and now only bike", "bik"),
        ("My standing desk arrived and my back feels better", "standing"),
        ("Where do I live now?", "chicago"),
        ("What do I drink?", "oat milk"),
        ("What team am I on?", "data platform"),
        ("Do I still drive?", "bik"),
        ("How is my back?", "standing"),
        ("What happened to my car?", "sold"),
        ("I started a YouTube channel called Data With Jordan", "data with jordan"),
        ("Priya became Chief of Emergency Medicine", "chief"),
        ("I adopted a kitten named Noodle", "noodle"),
        ("What is my YouTube channel called?", "data with jordan"),
        ("What is Priya's new title?", "chief"),
        ("What is my cat's name?", "noodle"),
        ("I'm now a team lead with 4 direct reports", "team lead"),
        ("My direct reports are Lisa, Marco, Aisha, and Dev", "aisha"),
        ("What is my role at work?", "team lead"),
        ("Who are my direct reports?", "aisha"),
        ("I got my AWS certification", "aws"),
        ("What certification do I have?", "aws"),
        ("I'm reading Project Hail Mary", "hail mary"),
        ("What book am I reading?", "hail mary"),
    ]
    for msg, v in more_updates:
        interactions.append({"cat": "update_recall", "msg": msg, "expect": "update/recall", "validate": v})

    # Phase 7: Final deep recall and synthesis (451-500)
    final_qs = [
        ("Start from the beginning — what is my full name?", "jordan"),
        ("Where have I lived throughout our conversation?", "austin"),
        ("What are all my job titles in order?", "data analyst"),
        ("What is my complete commute history?", "metrorail"),
        ("What are all my project changes?", "churn"),
        ("What happened to my car?", "civic"),
        ("Summarize my family situation", "priya"),
        ("What are all my pets?", "biscuit"),
        ("What is my current address situation?", "chicago"),
        ("What are my current and past drink preferences?", "dark roast"),
        ("What editors have I used?", "vs code"),
        ("What project trackers have I used?", "jira"),
        ("What is my full name?", "jordan"),
        ("Where do I currently live?", "chicago"),
        ("What is my nickname preference?", "jd"),
        ("What certification did I get?", "aws"),
        ("What book am I reading?", "hail mary"),
        ("How many pets do I have?", "biscuit"),
        ("What is my wife's job?", "chief"),
        ("What is my YouTube channel?", "data with jordan"),
        ("Who are my direct reports?", "aisha"),
        ("What do I drink now?", "oat milk"),
        ("What is my project?", "data platform"),
        ("Am I a team lead?", "team lead"),
        ("What happened to my gym membership?", "cancel"),
        ("What kind of coffee do I like?", "oat milk"),
        ("What happened to my car?", "sold"),
        ("Where did Biscuit hurt her paw?", "dog"),
        ("What is Noodle?", "kitten"),
        ("What cloud certification do I have?", "aws"),
        ("Do I still use Jira?", "linear"),
        ("What is my parking spot?", "h-09"),
        ("How do I commute?", "bik"),
        ("What is my Dell email?", "jordan.mitchell"),
        ("What happened to Karen Walsh?", "retired"),
        ("Who replaced Karen?", "tom"),
        ("What is my standing desk brand?", "uplift"),
        ("What happened to my Civic?", "sold"),
        ("How many direct reports do I have?", "4"),
        ("What is my birthday month?", "june"),
        ("What city did I move to from Austin?", "denver"),
        ("Where do I live now?", "chicago"),
        ("What was my commute before biking?", "metrorail"),
        ("What is my cat's name?", "noodle"),
        ("Am I allergic to anything?", "shellfish"),
        ("What is my best friend's name?", "tyler"),
        ("What is my dog's name?", "biscuit"),
        ("What author do I like?", "tchaikovsky"),
        ("What kind of dog is Biscuit?", "golden"),
        ("What is my wife's specialty?", "emergency"),
    ]
    for msg, v in final_qs:
        interactions.append({"cat": "final_recall", "msg": msg, "expect": "final recall", "validate": v})

    long_run_qs = [
        ("What is my full name?", "jordan"),
        ("Where do I currently live?", "chicago"),
        ("Where did I live before Chicago?", "denver"),
        ("Where did I live originally?", "austin"),
        ("What is my wife's name?", "priya"),
        ("What is Priya's current role?", "chief"),
        ("What is my dog's name?", "biscuit"),
        ("What is my cat's name?", "noodle"),
        ("What am I allergic to?", "shellfish"),
        ("What is my current commute?", "bik"),
        ("What was my old commute?", "metrorail"),
        ("What is my current drink preference?", "oat milk"),
        ("What did I drink before oat milk lattes?", "green tea"),
        ("What did I drink originally?", "dark roast"),
        ("What is my current project?", "data platform"),
        ("What project did I work on before Data Platform?", "pipeline"),
        ("What project did I work on originally?", "churn"),
        ("What editor do I use now?", "neovim"),
        ("What editor did I use before Neovim?", "vs code"),
        ("What project tracker do I use now?", "linear"),
        ("What tracker did I use before Linear?", "jira"),
        ("What is my current role?", "team lead"),
        ("What was my original role?", "data analyst"),
        ("What certification did I get?", "aws"),
        ("What book am I reading?", "hail mary"),
        ("What is my YouTube channel called?", "data with jordan"),
        ("Who are my direct reports?", "aisha"),
        ("How many direct reports do I have?", "4"),
        ("What happened to my Honda Civic?", "sold"),
        ("What is my current phone number?", "8899"),
        ("What is my nickname preference?", "jd"),
        ("Who is my current manager?", "tom"),
        ("Who was my previous manager?", "karen"),
        ("What happened to Karen Walsh?", "retired"),
        ("What is my parking spot?", "h-09"),
        ("What is my favorite lunch spot?", "torchy"),
        ("What genre do I read?", "sci-fi"),
        ("What author do I like?", "tchaikovsky"),
        ("Who is my best friend?", "tyler"),
        ("What is my Dell email?", "jordan.mitchell"),
        ("Summarize my location history", "austin"),
        ("Summarize my commute history", "metrorail"),
        ("Summarize my career changes", "senior"),
        ("Summarize my drink preference changes", "oat milk"),
        ("Summarize my tools and editors", "neovim"),
        ("Summarize my family and pets", "priya"),
        ("What are all my current key facts?", "chicago"),
        ("What are my current preferences?", "oat milk"),
        ("What should you remember about me?", "jordan"),
        ("What has changed about me since the start?", "chicago"),
    ]
    i = 0
    while len(interactions) < 500:
        msg, v = long_run_qs[i % len(long_run_qs)]
        interactions.append({"cat": "long_run", "msg": msg, "expect": "long-run recall", "validate": v})
        i += 1

    return interactions[:500]


INTERACTIONS = build_interactions()
MILESTONES = [100, 200, 300, 400, 500]


async def run_single_turn(ws: Any, idx: int, interaction: dict[str, str]) -> TurnResult:
    result = TurnResult(
        interaction_id=idx,
        category=interaction["cat"],
        input_message=interaction["msg"],
        expected_behavior=interaction["expect"],
        validate_token=interaction["validate"],
    )
    msg = {"type": "user_message", "content": interaction["msg"], "user_id": USER_ID}
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
            result.validation_detail = f"Found '{validate_token}'"
        else:
            result.validation_passed = False
            result.validation_detail = f"Missing '{validate_token}' ({len(result.response_text)} chars)"
    else:
        result.validation_passed = result.got_done and not result.got_error and result.got_text
        result.validation_detail = "No token — OK if no error"
    return result


async def run_all() -> list[TurnResult]:
    results: list[TurnResult] = []
    start_time = time.monotonic()

    print(f"Connecting to {WS_URL} ...")
    async with websockets.connect(WS_URL, ping_interval=30, ping_timeout=60, close_timeout=30) as ws:
        print(f"Connected! Running {len(INTERACTIONS)} interactions...\n")

        for idx, interaction in enumerate(INTERACTIONS, 1):
            result = await asyncio.wait_for(
                run_single_turn(ws, idx, interaction), timeout=TIMEOUT_PER_TURN
            )
            results.append(result)

            if idx % 25 == 0 or not result.validation_passed or result.got_error:
                elapsed = (time.monotonic() - start_time) / 60
                status = "OK" if result.validation_passed else "FAIL"
                f" [{', '.join(result.tool_names[:2])}]" if result.tool_names else ""
                print(
                    f"[{idx:3d}/500] {status} | {result.category:<16s} | "
                    f"{result.elapsed_ms:6.0f}ms | {elapsed:.1f}m"
                    + (f" | {result.validation_detail[:55]}" if not result.validation_passed else "")
                )

            if idx in MILESTONES:
                elapsed = (time.monotonic() - start_time) / 60
                passed = sum(1 for r in results if r.validation_passed)
                total = len(results)
                errors = sum(1 for r in results if r.got_error)
                used_tools = sum(1 for r in results if r.got_tool_call)
                cats: dict[str, list[TurnResult]] = {}
                for r in results:
                    cats.setdefault(r.category, []).append(r)
                print(f"\n{'=' * 70}")
                print(f"  MILESTONE: {idx} turns | {elapsed:.1f}min")
                print(f"  Overall: {passed}/{total} ({passed/total*100:.1f}%) | Errors: {errors} | Tools: {used_tools}")
                for cat in sorted(cats.keys()):
                    cr = cats[cat]
                    cp = sum(1 for r in cr if r.validation_passed)
                    print(f"    {cat:<20s}: {cp}/{len(cr)} ({cp/len(cr)*100:.0f}%)")
                print(f"{'=' * 70}\n")

    return results


def print_final_report(results: list[TurnResult]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.validation_passed)
    errors = sum(1 for r in results if r.got_error)
    got_done = sum(1 for r in results if r.got_done)
    used_tools = sum(1 for r in results if r.got_tool_call)
    avg_ms = sum(r.elapsed_ms for r in results) / total if total else 0
    total_min = sum(r.elapsed_ms for r in results) / 60000

    print("\n" + "=" * 80)
    print("PROGRESSIVE WS MEMORY TEST — FINAL REPORT")
    print("=" * 80)
    print(f"\nOverall: {passed}/{total} ({passed/total*100:.1f}%)")
    print(f"Errors: {errors}/{total} | Got 'done': {got_done}/{total}")
    print(f"Tool usage: {used_tools}/{total} | Avg latency: {avg_ms:.0f}ms | Total: {total_min:.1f}min")

    # Milestone analysis
    print("\n--- Accuracy Degradation Over Time ---")
    for milestone in MILESTONES:
        if milestone > total:
            break
        milestone_results = results[:milestone]
        mp = sum(1 for r in milestone_results if r.validation_passed)
        print(f"  Turn 1-{milestone:>3d}: {mp}/{milestone} ({mp/milestone*100:.1f}%)")

    # By category
    print("\n--- By Category ---")
    cats: dict[str, list[TurnResult]] = {}
    for r in results:
        cats.setdefault(r.category, []).append(r)
    for cat in sorted(cats.keys()):
        cr = cats[cat]
        cp = sum(1 for r in cr if r.validation_passed)
        ct = len(cr)
        ce = sum(1 for r in cr if r.got_error)
        ct2 = sum(1 for r in cr if r.got_tool_call)
        ca = sum(r.elapsed_ms for r in cr) / ct if ct else 0
        print(f"  {cat:<20s}: {cp}/{ct} ({cp/ct*100:.0f}%) | err={ce} tools={ct2} avg={ca:.0f}ms")

    # Tool stats
    print("\n--- Tool Usage ---")
    all_tools: dict[str, int] = {}
    for r in results:
        for t in r.tool_names:
            if t:
                all_tools[t] = all_tools.get(t, 0) + 1
    for tool, count in sorted(all_tools.items(), key=lambda x: -x[1])[:10]:
        print(f"  {tool:<30s}: {count}")

    # Failures
    print("\n--- Sample Failures (first 15) ---")
    failures = [r for r in results if not r.validation_passed]
    for r in failures[:15]:
        print(f"  [{r.interaction_id:3d}] {r.category:<16s} | {r.input_message[:50]:<50s} | {r.validation_detail[:60]}")

    # Save results
    report_path = "data/benchmarks/results/ws_progressive_results.json"
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump([{
            "id": r.interaction_id, "category": r.category, "input": r.input_message,
            "passed": r.validation_passed, "detail": r.validation_detail,
            "got_done": r.got_done, "got_error": r.got_error, "tools": r.tool_names,
            "elapsed_ms": round(r.elapsed_ms),
        } for r in results], f, indent=2)
    print(f"\nResults saved to {report_path}")


async def main() -> None:
    results = await run_all()
    print_final_report(results)

if __name__ == "__main__":
    asyncio.run(main())
