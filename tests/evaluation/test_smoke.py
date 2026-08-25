"""Quick 25-persona smoke test — 2 queries per persona via REST + 1 streaming test."""

import asyncio
import json
import os
import time

import aiohttp
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_HTTP_EVALS") != "1",
    reason="set RUN_HTTP_EVALS=1 to run live HTTP evaluation tests",
)

HTTP_BASE_URL = "http://localhost:8080"

PERSONAS = [
    {"id": "p1", "name": "Direct Dave"},
    {"id": "p2", "name": "Polite Pam"},
    {"id": "p3", "name": "Casual Chris"},
    {"id": "p4", "name": "Questioning Quinn"},
    {"id": "p5", "name": "Storytelling Sam"},
    {"id": "p6", "name": "Commanding Chris"},
    {"id": "p7", "name": "Emoji Eva"},
    {"id": "p8", "name": "Minimalist Mike"},
    {"id": "p9", "name": "Technical Terry"},
    {"id": "p10", "name": "Confused Clara"},
    {"id": "p11", "name": "Analytical Alex"},
    {"id": "p12", "name": "Efficient Eddie"},
    {"id": "p13", "name": "Verbose Victor"},
    {"id": "p14", "name": "Curious Casey"},
    {"id": "p15", "name": "Busy Brian"},
    {"id": "p16", "name": "Organized Olivia"},
    {"id": "p17", "name": "Flexible Fran"},
    {"id": "p18", "name": "Goal-Oriented Gary"},
    {"id": "p19", "name": "Collaborative Carol"},
    {"id": "p20", "name": "Privacy-First Paul"},
    {"id": "p21", "name": "Quick Quinn"},
    {"id": "p22", "name": "Deep Diver"},
    {"id": "p23", "name": "Error-Prone Eddie"},
    {"id": "p24", "name": "Context Carter"},
    {"id": "p25", "name": "Mixed Mike"},
]

QUERIES_PER_PERSONA = {
    "p1": ["what time is it", "list my files"],
    "p2": ["Could you tell me the time please?", "What skills are available?"],
    "p3": ["hey what time is it", "show me my files"],
    "p4": ["What time is it? How does it work?", "Can you list my subagents?"],
    "p5": ["I need to know the time in Tokyo for my meeting", "Search web for latest AI developments"],
    "p6": ["Time now.", "List files."],
    "p7": ["What time is it? ⏰", "Show my files! 📁"],
    "p8": ["time", "files"],
    "p9": ["Execute time_get with timezone UTC", "Execute skills_list"],
    "p10": ["How do I check the time?", "What files do I have?"],
    "p11": ["Time in UTC, EST, and PST", "List files with details"],
    "p12": ["Quick - what time is it", "fast file list"],
    "p13": ["Provide a detailed overview of the current time", "Comprehensive list of all available skills"],
    "p14": ["What time is it? Can I also check other timezones?", "What skills exist? Any others?"],
    "p15": ["What time is it? Also list files", "Search web for news and list skills"],
    "p16": ["Organize: list all available skills", "What time is it in major timezones?"],
    "p17": ["What time is it? Actually, list files instead.", "Or show me available skills."],
    "p18": ["Current time? What's our progress?", "List subagents and their status"],
    "p19": ["Team time check - what time works?", "What skills help with collaboration?"],
    "p20": ["Time? Don't log this.", "List files privately."],
    "p21": ["time", "skills"],
    "p22": ["Search web for Python asyncio and summarize", "Create subagent named researcher with web_search tool"],
    "p23": ["Time in Invalid/Timezone?", "Search with regex /*bad[*/"],
    "p24": ["What time is it? Remember my timezone.", "Files I mentioned before?"],
    "p25": ["What time is it?", "What skills do I have?"],
}


async def test_streaming():
    print("=" * 60)
    print("STREAMING TEST (SSE)")
    print("=" * 60)
    queries = [
        ("Simple text", "What time is it in UTC?"),
        ("Tool call", "List my files"),
    ]
    for name, query in queries:
        print(f"  {name}: {query[:50]}...")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                payload = {"message": query, "user_id": "stream_test"}
                start = time.time()
                event_types = []
                async with session.post(f"{HTTP_BASE_URL}/message/stream", json=payload) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        print(f"    ERROR: HTTP {resp.status}: {text[:100]}")
                        continue
                    async for line in resp.content:
                        decoded = line.decode("utf-8", errors="replace").strip()
                        if decoded.startswith("data:"):
                            try:
                                event = json.loads(decoded[5:].strip())
                                t = event.get("type", "unknown")
                                event_types.append(t)
                            except json.JSONDecodeError:
                                event_types.append("parse_error")
                duration = int((time.time() - start) * 1000)
                unique_types = sorted(set(event_types))
                print(f"    {duration}ms | {len(event_types)} events | Types: {', '.join(unique_types)}")
                has_text = any(t in unique_types for t in ["text_start", "text_delta", "text_end", "ai_token"])
                has_done = "done" in unique_types
                print(f"    Text events: {has_text} | Done event: {has_done}")
        except Exception as e:
            print(f"    ERROR: {str(e)[:100]}")
    print("  Streaming test COMPLETE.\n")


async def test_all_personas():
    await test_streaming()

    print("=" * 60)
    print("25-PERSONA SMOKE TEST (2 queries each)")
    print("=" * 60)

    all_results = []
    total_passed = 0
    total_failed = 0

    for persona in PERSONAS:
        queries = QUERIES_PER_PERSONA.get(persona["id"], ["What time is it?"])
        print(f"\n[{persona['id']}] {persona['name']} — {len(queries)} queries")

        for query in queries:
            user_id = f"ptest_{persona['id']}"
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                    payload = {"message": query, "user_id": user_id}
                    start = time.time()
                    async with session.post(f"{HTTP_BASE_URL}/message", json=payload) as resp:
                        duration = int((time.time() - start) * 1000)
                        data = await resp.json()
                        response = data.get("response", "")
                        success = len(response) > 10
                        status = "✅" if success else "❌"
                        preview = response[:60].replace("\n", " ") if success else (data.get("error", "empty")[:60])
                        print(f"  {status} {duration:5d}ms | {query[:35]:35s} | {preview}")
                        all_results.append({"id": persona["id"], "name": persona["name"],
                                            "query": query, "success": success, "duration_ms": duration,
                                            "response_len": len(response)})
                        if success:
                            total_passed += 1
                        else:
                            total_failed += 1
            except Exception as e:
                print(f"  ❌ ERR | {query[:35]:35s} | {str(e)[:60]}")
                all_results.append({"id": persona["id"], "name": persona["name"],
                                    "query": query, "success": False, "duration_ms": 0, "error": str(e)[:100]})
                total_failed += 1

            await asyncio.sleep(0.3)

    total = total_passed + total_failed
    print(f"\n{'=' * 60}")
    print(f"RESULTS: {total_passed}/{total} passed ({100*total_passed/total:.0f}%) | ⏱ {total_failed} failed")
    print("=" * 60)

    # Save
    from pathlib import Path
    Path("data/evaluations").mkdir(parents=True, exist_ok=True)
    with open("data/evaluations/persona_smoke_test.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print("Saved to data/evaluations/persona_smoke_test.json")

    # Failures
    if total_failed > 0:
        print("\n❌ Failures:")
        for r in all_results:
            if not r.get("success", False):
                print(f"  [{r['id']}] {r.get('query', '?')[:50]} — {r.get('error', 'empty response')[:80]}")


if __name__ == "__main__":
    asyncio.run(test_all_personas())
