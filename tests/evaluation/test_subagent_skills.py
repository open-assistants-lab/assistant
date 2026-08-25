"""Focused tests for subagents and skills functionality (V1)."""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_HTTP_EVALS") != "1",
    reason="set RUN_HTTP_EVALS=1 to run live HTTP evaluation tests",
)

HTTP_BASE_URL = "http://localhost:8080"


@dataclass
class TestResult:
    name: str
    query: str
    response: str
    success: bool
    error: str | None = None
    duration_ms: int = 0


async def call_agent(message: str, user_id: str = "test_subagent") -> dict:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        payload = {"message": message, "user_id": user_id}
        async with session.post(f"{HTTP_BASE_URL}/message", json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"HTTP {resp.status}: {text}")
            return await resp.json()


async def test_subagent_create():
    results = []
    tests = [
        ("Create research subagent", "Create a subagent named 'researcher' with skill-creator skill"),
        ("Create coding subagent", "Create a coding subagent named 'coder' that helps with programming"),
    ]
    for name, query in tests:
        start = time.time()
        try:
            resp = await call_agent(query)
            response = resp.get("response", "")
            success = "subagent" in response.lower() or "created" in response.lower()
            results.append(TestResult(name=name, query=query, response=response, success=success, duration_ms=int((time.time() - start) * 1000)))
        except Exception as e:
            results.append(TestResult(name=name, query=query, response="", success=False, error=str(e), duration_ms=int((time.time() - start) * 1000)))
    return results


async def test_subagent_start():
    results = []
    await call_agent("Create a subagent named 'quick-test' with web_search and files_read tools")
    await asyncio.sleep(1)
    tests = [
        ("Start subagent", "Start subagent 'quick-test' to search for AI news"),
        ("Start with specific task", "Start quick-test to find information about Python asyncio"),
    ]
    for name, query in tests:
        start = time.time()
        try:
            resp = await call_agent(query)
            response = resp.get("response", "")
            success = len(response) > 50
            results.append(TestResult(name=name, query=query, response=response, success=success, duration_ms=int((time.time() - start) * 1000)))
        except Exception as e:
            results.append(TestResult(name=name, query=query, response="", success=False, error=str(e), duration_ms=int((time.time() - start) * 1000)))
    return results


async def test_subagent_update():
    results = []
    tests = [
        ("Update subagent", "Update subagent 'quick-test' to add message_search to allowed tools"),
    ]
    for name, query in tests:
        start = time.time()
        try:
            resp = await call_agent(query)
            response = resp.get("response", "")
            success = "updated" in response.lower() or "subagent" in response.lower()
            results.append(TestResult(name=name, query=query, response=response, success=success, duration_ms=int((time.time() - start) * 1000)))
        except Exception as e:
            results.append(TestResult(name=name, query=query, response="", success=False, error=str(e), duration_ms=int((time.time() - start) * 1000)))
    return results


async def test_subagent_list():
    results = []
    tests = [("List all subagents", "List all my subagents")]
    for name, query in tests:
        start = time.time()
        try:
            resp = await call_agent(query)
            response = resp.get("response", "")
            success = "subagent" in response.lower() or len(response) > 0
            results.append(TestResult(name=name, query=query, response=response, success=success, duration_ms=int((time.time() - start) * 1000)))
        except Exception as e:
            results.append(TestResult(name=name, query=query, response="", success=False, error=str(e), duration_ms=int((time.time() - start) * 1000)))
    return results


async def test_subagent_tasks():
    results = []
    tests = [("List subagent tasks", "Show any active subagent tasks")]
    for name, query in tests:
        start = time.time()
        try:
            resp = await call_agent(query)
            response = resp.get("response", "")
            success = "task" in response.lower() or "subagent" in response.lower()
            results.append(TestResult(name=name, query=query, response=response, success=success, duration_ms=int((time.time() - start) * 1000)))
        except Exception as e:
            results.append(TestResult(name=name, query=query, response="", success=False, error=str(e), duration_ms=int((time.time() - start) * 1000)))
    return results


async def test_subagent_instruct():
    results = []
    tests = [("Instruct subagent", "Instruct subagent 'quick-test' to also check arxiv for papers")]
    for name, query in tests:
        start = time.time()
        try:
            resp = await call_agent(query)
            response = resp.get("response", "")
            success = "instruction" in response.lower() or "subagent" in response.lower()
            results.append(TestResult(name=name, query=query, response=response, success=success, duration_ms=int((time.time() - start) * 1000)))
        except Exception as e:
            results.append(TestResult(name=name, query=query, response="", success=False, error=str(e), duration_ms=int((time.time() - start) * 1000)))
    return results


async def test_subagent_cancel():
    results = []
    tests = [("Cancel subagent task", "Cancel any running subagent tasks")]
    for name, query in tests:
        start = time.time()
        try:
            resp = await call_agent(query)
            response = resp.get("response", "")
            success = "cancel" in response.lower() or "subagent" in response.lower()
            results.append(TestResult(name=name, query=query, response=response, success=success, duration_ms=int((time.time() - start) * 1000)))
        except Exception as e:
            results.append(TestResult(name=name, query=query, response="", success=False, error=str(e), duration_ms=int((time.time() - start) * 1000)))
    return results


async def test_subagent_delete():
    results = []
    tests = [("Delete subagent", "Delete subagent 'quick-test'")]
    for name, query in tests:
        start = time.time()
        try:
            resp = await call_agent(query)
            response = resp.get("response", "")
            success = "deleted" in response.lower() or "subagent" in response.lower()
            results.append(TestResult(name=name, query=query, response=response, success=success, duration_ms=int((time.time() - start) * 1000)))
        except Exception as e:
            results.append(TestResult(name=name, query=query, response="", success=False, error=str(e), duration_ms=int((time.time() - start) * 1000)))
    return results


async def test_skills_list():
    results = []
    tests = [("List all skills", "List all available skills"), ("Show skills detail", "What skills do you have?")]
    for name, query in tests:
        start = time.time()
        try:
            resp = await call_agent(query)
            response = resp.get("response", "")
            success = "skill" in response.lower() or len(response) > 0
            results.append(TestResult(name=name, query=query, response=response, success=success, duration_ms=int((time.time() - start) * 1000)))
        except Exception as e:
            results.append(TestResult(name=name, query=query, response="", success=False, error=str(e), duration_ms=int((time.time() - start) * 1000)))
    return results


async def test_skills_load():
    results = []
    tests = [
        ("Load skill creator", "Load the skill-creator skill"),
        ("Load skill creator", "Load the skill-creator skill"),
        ("Load skill creator", "Load the skill-creator skill"),
    ]
    for name, query in tests:
        start = time.time()
        try:
            resp = await call_agent(query)
            response = resp.get("response", "")
            success = "skill" in response.lower() or "load" in response.lower() or len(response) > 0
            results.append(TestResult(name=name, query=query, response=response, success=success, duration_ms=int((time.time() - start) * 1000)))
        except Exception as e:
            results.append(TestResult(name=name, query=query, response="", success=False, error=str(e), duration_ms=int((time.time() - start) * 1000)))
    return results


async def test_skill_create():
    results = []
    tests = [
        ("Create simple skill", "Create a new skill named 'test-hello' that says hello world"),
        ("Create skill with description", "Create a skill called 'email-helper' for helping with emails"),
    ]
    for name, query in tests:
        start = time.time()
        try:
            resp = await call_agent(query)
            response = resp.get("response", "")
            success = "skill" in response.lower() or "created" in response.lower()
            results.append(TestResult(name=name, query=query, response=response, success=success, duration_ms=int((time.time() - start) * 1000)))
        except Exception as e:
            results.append(TestResult(name=name, query=query, response="", success=False, error=str(e), duration_ms=int((time.time() - start) * 1000)))
    return results


async def test_subagent_with_skills():
    results = []
    tests = [
        ("Create with skill-creator", "Create a subagent named 'tool-builder' with skill-creator skill"),
        ("Create with skill-creator", "Create a subagent with skill-creator skill"),
    ]
    for name, query in tests:
        start = time.time()
        try:
            resp = await call_agent(query)
            response = resp.get("response", "")
            success = "subagent" in response.lower() or "created" in response.lower()
            results.append(TestResult(name=name, query=query, response=response, success=success, duration_ms=int((time.time() - start) * 1000)))
        except Exception as e:
            results.append(TestResult(name=name, query=query, response="", success=False, error=str(e), duration_ms=int((time.time() - start) * 1000)))
    return results


async def run_all_tests():
    output_dir = Path("data/evaluations")
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("SUBAGENT V1 AND SKILLS FOCUSED TESTS")
    print("=" * 60)

    all_results = {}

    test_fns = [
        ("subagent_create", test_subagent_create),
        ("subagent_start", test_subagent_start),
        ("subagent_update", test_subagent_update),
        ("subagent_list", test_subagent_list),
        ("subagent_tasks", test_subagent_tasks),
        ("subagent_instruct", test_subagent_instruct),
        ("subagent_cancel", test_subagent_cancel),
        ("subagent_delete", test_subagent_delete),
        ("skills_list", test_skills_list),
        ("skills_load", test_skills_load),
        ("skill_create", test_skill_create),
        ("subagent_with_skills", test_subagent_with_skills),
    ]

    for i, (key, fn) in enumerate(test_fns, 1):
        print(f"\n[{i}/{len(test_fns)}] Testing {key}...")
        all_results[key] = await fn()
        for r in all_results[key]:
            status = "✅" if r.success else "❌"
            print(f"  {status} {r.name}: {r.duration_ms}ms")

    total = sum(len(rs) for rs in all_results.values())
    passed = sum(1 for rs in all_results.values() for r in rs if r.success)
    print(f"\n{'=' * 60}")
    print(f"SUMMARY: {passed}/{total} tests passed ({100 * passed / total:.1f}%)")
    print("=" * 60)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"subagent_skills_test_{timestamp}.json"
    json_results = {cat: [{"name": r.name, "query": r.query, "success": r.success, "error": r.error, "duration_ms": r.duration_ms} for r in rs] for cat, rs in all_results.items()}
    with open(output_file, "w") as f:
        json.dump(json_results, f, indent=2)
    print(f"\nResults saved to {output_file}")

    return all_results


if __name__ == "__main__":
    asyncio.run(run_all_tests())
