"""25-Persona HTTP Integration Test for Assistant.

Runs each persona through a focused set of queries via POST /message.
Streaming is tested separately (once) since it doesn't need per-persona repetition.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass

import aiohttp
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_HTTP_EVALS") != "1",
    reason="set RUN_HTTP_EVALS=1 to run live HTTP evaluation tests",
)

HTTP_BASE_URL = "http://localhost:8080"

# ─── 25 Personas (from TEST.md) ───

PERSONAS = [
    {"id": "p1", "name": "Direct Dave", "style": "terse"},
    {"id": "p2", "name": "Polite Pam", "style": "formal"},
    {"id": "p3", "name": "Casual Chris", "style": "casual"},
    {"id": "p4", "name": "Questioning Quinn", "style": "inquisitive"},
    {"id": "p5", "name": "Storytelling Sam", "style": "narrative"},
    {"id": "p6", "name": "Commanding Chris", "style": "authoritative"},
    {"id": "p7", "name": "Emoji Eva", "style": "expressive"},
    {"id": "p8", "name": "Minimalist Mike", "style": "minimal"},
    {"id": "p9", "name": "Technical Terry", "style": "technical"},
    {"id": "p10", "name": "Confused Clara", "style": "uncertain"},
    {"id": "p11", "name": "Analytical Alex", "style": "analytical"},
    {"id": "p12", "name": "Efficient Eddie", "style": "efficient"},
    {"id": "p13", "name": "Verbose Victor", "style": "verbose"},
    {"id": "p14", "name": "Curious Casey", "style": "curious"},
    {"id": "p15", "name": "Busy Brian", "style": "busy"},
    {"id": "p16", "name": "Organized Olivia", "style": "organized"},
    {"id": "p17", "name": "Flexible Fran", "style": "flexible"},
    {"id": "p18", "name": "Goal-Oriented Gary", "style": "goal_oriented"},
    {"id": "p19", "name": "Collaborative Carol", "style": "collaborative"},
    {"id": "p20", "name": "Privacy-First Paul", "style": "privacy"},
    {"id": "p21", "name": "Quick Quinn", "style": "quick"},
    {"id": "p22", "name": "Deep Diver", "style": "deep"},
    {"id": "p23", "name": "Error-Prone Eddie", "style": "error"},
    {"id": "p24", "name": "Context Carter", "style": "context"},
    {"id": "p25", "name": "Mixed Mike", "style": "mixed"},
]

# ─── Feature-Domain Queries ───
# Each query maps to a feature domain from TEST.md

DOMAIN_QUERIES = {
    "core_time": ["what time is it?", "what time is it in Tokyo?"],
    "core_shell": ["run python3 -c 'print(2+2)'"],
    "filesystem_list": ["list my files"],
    "filesystem_read": ["read the file test.txt"],
    "filesystem_write": ["create a file called hello.txt with content Hello World"],
    "filesystem_search": ["find all Python files", "search for TODO in files"],
    "memory": ["what did we talk about before?", "search my memory for Python"],
    "skills_list": ["what skills are available?"],
    "skills_load": ["load the skill-creator skill"],
    "web_search": ["search the web for Python tutorials"],
    "apps": ["create a library app with books table having title TEXT and description TEXT"],
    "subagent_create": ["create a subagent named researcher with web_search and files_read tools"],
    "subagent_list": ["list my subagents"],
}

# ─── Per-Persona Query Sets ───
# Each persona gets 5 queries targeting their style's feature domains

PERSONA_QUERIES = {
    "p1": ["what time is it", "list my files", "search web for AI news", "create subagent named dev with shell_execute", "list my subagents"],
    "p2": ["Could you please tell me the current time?", "Would you list my files?", "I would like to search the web for Python tutorials please.", "Can you list available skills?", "Please list my subagents."],
    "p3": ["hey what time is it", "show me my files real quick", "search web for latest tech news", "what skills do I have", "list subagents"],
    "p4": ["What time is it? How does timezone support work?", "Can you list my files? What formats are supported?", "What skills are available? Can you explain each?", "How do I create a subagent? What tools can I give it?", "What subagents do I have? How do I check a subagent job?"],
    "p5": ["I've been working on a project and need to know what time it is in New York", "I have some files from my research - can you show me what's there?", "I heard about interesting AI developments - can you search for recent news?", "I'm exploring what capabilities you have - what skills are available?", "I set up some subagents earlier for my research - can you list them?"],
    "p6": ["Time. Now.", "List all files immediately.", "Search web for AI trends.", "List available skills.", "List all subagents."],
    "p7": ["What time is it? ⏰", "Show me my files! 📁", "Search the web for cool stuff! 🔍", "What skills do you have? ✨", "List my subagents! 🤖"],
    "p8": ["time", "files", "search web AI", "skills", "subagents"],
    "p9": ["Execute time_get with timezone parameter UTC", "Execute files_list for current user directory", "Execute web_search with query parameter 'machine learning benchmarks'", "Execute skills_list to enumerate available capabilities", "Execute subagent_list to enumerate active subagent definitions"],
    "p10": ["I'm not sure how to check the time... can you help?", "How do I see my files? I'm confused about where they are.", "Can you help me search the web? I need info about Python.", "What skills can I use? I don't know what's available.", "I think I made some subagents? How do I check?"],
    "p11": ["What time is it across multiple timezones? Give me UTC, EST, PST.", "List my files with metadata - sizes, dates, types.", "Search the web for AI benchmarks 2026 with statistics.", "List skills with descriptions and tool counts.", "List subagents with their configurations and tool counts."],
    "p12": ["Quick - current time", "fast file list", "web search AI", "skills list", "subagents list"],
    "p13": ["Could you please provide a comprehensive overview of the current time including timezone details?", "I would like a detailed listing of all files in my workspace, including any subdirectories.", "Please conduct a thorough web search about the latest developments in artificial intelligence.", "Provide a complete enumeration of all available skills with their descriptions.", "List all subagents with full details about their configurations."],
    "p14": ["What time is it? Can I also check other timezones?", "What files do I have? Can I search by pattern too?", "Search web for Python - also what about Rust?", "What skills are available? Can I also create custom ones?", "List subagents - can I also update existing ones?"],
    "p15": ["What time is it? Also list my files", "Search web for AI news while I check my skills", "Create subagent named researcher with web_search, also list existing subagents", "What time is it in London? Also Tokyo?", "List files and search web for Python"],
    "p16": ["Organize my files by listing them with details.", "Categorize available skills by type.", "Create a structured list of subagents.", "What time is it in all major timezones - organized by UTC offset.", "Search web and organize results by relevance."],
    "p17": ["What time is it? Actually, let's list files instead.", "Maybe just show me what subagents I have.", "Or search the web for something interesting.", "Whichever skill is most useful right now.", "I changed my mind - just tell me the time."],
    "p18": ["What's the current time? Tracking timezone goals.", "List files - what's my progress on file organization?", "Search web for productivity tools.", "What skills can help track my goals?", "Check subagent job - are tasks completing?"],
    "p19": ["What time works for the team? List files we can share.", "Search web for collaboration tools.", "What skills help with team workflows?", "Create subagent for team research.", "List subagents the team has set up."],
    "p20": ["What time is it? Don't log this.", "List files - keep it private.", "Search web but don't store results.", "What skills are available? Are they secure?", "List subagents - who has access?"],
    "p21": ["time", "files", "web search Python", "skills", "subagents"],
    "p22": ["Research AI trends, search the web, and give me a comprehensive summary with key findings", "Create subagent researcher with web_search and files_read tools, then list all subagents to verify", "Find all Python files in my workspace, read their contents, and summarize what each does", "Load skill-creator skill, then outline a multi-step research plan for machine learning", "What time is it locally? Also search the web for recent breakthroughs in quantum computing"],
    "p23": ["Connect to email invalid@notreal.xyz with password fake123", "Search with regex pattern /*invalid[*/", "Delete the file /tmp/nonexistent_file_that_does_not_exist.txt", "Create subagent with name containing special chars: test@#$%", "What time is it in the timezone Invalid/Timezone?"],
    "p24": ["What time is it? Remember my timezone preference.", "I added a file earlier - can you find it?", "Search my memory for what we discussed last time.", "What skills did I load before?", "Check the subagent I created yesterday."],
    "p25": ["What time is it?", "List my files", "Search web for weather today", "What skills do I have?", "Check subagent status"],
}


@dataclass
class TestResult:
    persona_id: str
    persona_name: str
    query: str
    success: bool
    response_preview: str
    duration_ms: int
    error: str | None = None


async def send_message(message: str, user_id: str = "persona_test") -> dict:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        payload = {"message": message, "user_id": user_id}
        start = time.time()
        async with session.post(f"{HTTP_BASE_URL}/message", json=payload) as resp:
            duration_ms = int((time.time() - start) * 1000)
            if resp.status != 200:
                text = await resp.text()
                return {"response": f"HTTP {resp.status}: {text[:200]}", "duration_ms": duration_ms, "error": True}
            data = await resp.json()
            return {
                "response": data.get("response", ""),
                "tool_calls": data.get("tool_calls", []),
                "duration_ms": duration_ms,
                "error": False,
            }


async def send_stream_message(message: str, user_id: str = "stream_test") -> dict:
    """Test SSE streaming — confirm block-structured events work."""
    events = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        payload = {"message": message, "user_id": user_id}
        start = time.time()
        async with session.post(f"{HTTP_BASE_URL}/message/stream", json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                return {"events": [], "error": f"HTTP {resp.status}: {text[:200]}", "duration_ms": 0}
            async for line in resp.content:
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded.startswith("data:"):
                    event_data = decoded[5:].strip()
                    if event_data:
                        try:
                            event = json.loads(event_data)
                            events.append(event.get("type", "unknown"))
                        except json.JSONDecodeError:
                            events.append(f"parse_error: {event_data[:50]}")
            duration_ms = int((time.time() - start) * 1000)
    return {"events": events, "error": None, "duration_ms": duration_ms}


async def test_streaming():
    """Test SSE streaming with a simple query to verify block-structured events."""
    print("\n" + "=" * 60)
    print("STREAMING TEST (SSE)")
    print("=" * 60)

    queries = [
        ("Simple text", "What time is it?"),
        ("Tool call", "List my files"),
        ("Another tool", "Search the web for Python asyncio"),
    ]

    for name, query in queries:
        print(f"\n  Stream: {name} — {query[:50]}...")
        result = await send_stream_message(query)
        if result["error"]:
            print(f"    ERROR: {result['error']}")
        else:
            events = result["events"]
            event_types = set(events)
            print(f"    Duration: {result['duration_ms']}ms")
            print(f"    Event count: {len(events)}")
            print(f"    Event types: {', '.join(sorted(event_types))}")

            has_text = any(t in event_types for t in ["text_start", "text_delta", "text_end", "ai_token"])
            has_done = "done" in event_types
            print(f"    Has text events: {has_text}")
            print(f"    Has done event: {has_done}")

    print("\n  Streaming test COMPLETE.")


async def run_persona_queries(persona: dict, queries: list[str]) -> list[TestResult]:
    results = []
    for query in queries:
        user_id = f"persona_{persona['id']}"
        try:
            resp = await send_message(query, user_id=user_id)
            response = resp.get("response", "")
            duration = resp.get("duration_ms", 0)
            is_error = resp.get("error", False)
            success = len(response) > 10 and not is_error
            results.append(TestResult(
                persona_id=persona["id"],
                persona_name=persona["name"],
                query=query,
                success=success,
                response_preview=response[:100],
                duration_ms=duration,
                error=None if success else (response[:200] if is_error else "Empty response"),
            ))
        except Exception as e:
            results.append(TestResult(
                persona_id=persona["id"],
                persona_name=persona["name"],
                query=query,
                success=False,
                response_preview="",
                duration_ms=0,
                error=str(e)[:200],
            ))
        await asyncio.sleep(0.5)  # Rate limit
    return results


async def run_all_tests():
    # ─── Phase 1: Streaming Test (once) ───
    await test_streaming()

    # ─── Phase 2: 25 Personas ───
    print("\n" + "=" * 60)
    print("25-PERSONA INTEGRATION TEST")
    print("=" * 60)

    all_results = []
    total_passed = 0
    total_failed = 0
    total_queries = 0

    for persona in PERSONAS:
        queries = PERSONA_QUERIES.get(persona["id"], ["What time is it?"])
        print(f"\n[{persona['id']}] {persona['name']} ({persona['style']}) — {len(queries)} queries")

        results = await run_persona_queries(persona, queries)
        all_results.extend(results)

        passed = sum(1 for r in results if r.success)
        failed = len(results) - passed
        total_passed += passed
        total_failed += failed
        total_queries += len(results)

        for r in results:
            status = "✅" if r.success else "❌"
            preview = r.response_preview[:60] if r.success else (r.error or "N/A")[:60]
            print(f"  {status} {r.duration_ms:5d}ms | {r.query[:40]:40s} | {preview}")

    # ─── Summary ───
    print("\n" + "=" * 60)
    print(f"SUMMARY: {total_passed}/{total_queries} passed ({100 * total_passed / total_queries if total_queries else 0:.1f}%)")
    print(f"  Passed: {total_passed}")
    print(f"  Failed: {total_failed}")
    print("=" * 60)

    # ─── Per-Persona breakdown ───
    print("\nPer-Persona Breakdown:")
    for persona in PERSONAS:
        persona_results = [r for r in all_results if r.persona_id == persona["id"]]
        p = sum(1 for r in persona_results if r.success)
        failed = len(persona_results) - p
        avg_ms = sum(r.duration_ms for r in persona_results) / len(persona_results) if persona_results else 0
        print(
            f"  {persona['id']:3s} {persona['name']:25s} | "
            f"{p}/{len(persona_results)} passed | {failed} failed | {avg_ms:.0f}ms avg"
        )

    # ─── Save results ───
    output_path = "data/evaluations/persona_integration_test.json"
    from pathlib import Path
    Path("data/evaluations").mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump([{"persona": r.persona_id, "name": r.persona_name, "query": r.query,
                     "success": r.success, "duration_ms": r.duration_ms, "error": r.error}
                    for r in all_results], f, indent=2)
    print(f"\nResults saved to {output_path}")

    if total_failed > 0:
        print("\n❌ Failed queries:")
        for r in all_results:
            if not r.success:
                print(f"  [{r.persona_id}] {r.query[:50]} — {r.error}")

    return all_results


if __name__ == "__main__":
    asyncio.run(run_all_tests())
