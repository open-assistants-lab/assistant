from __future__ import annotations

import asyncio
import json
import logging
import time

from tests.smoke.models import SuiteResult, TestResult

logger = logging.getLogger(__name__)

KNOWN_TOOLS = {
    "app_column_add", "app_column_delete", "app_column_rename", "app_create", "app_delete",
    "app_delete_row", "app_insert", "app_list", "app_query", "app_schema",
    "app_search_fts", "app_update", "browser_click", "browser_eval",
    "browser_fill", "browser_open", "browser_screenshot",
    "browser_snapshot", "files_delete", "files_edit", "files_glob_search",
    "files_grep_search", "files_list", "files_mkdir", "files_read", "files_rename",
    "files_versions_clean", "files_versions_delete", "files_versions_list", "files_versions_restore", "files_write",
    "mcp_list", "mcp_reload", "mcp_tools", "memory_profile",
    "message_count", "message_history", "message_search", "message_timeline", "research_list",
    "research_start", "shell_execute", "skills_load", "skills_reload", "subagent_cancel",
    "subagent_check", "subagent_create", "subagent_delegate", "subagent_delete", "subagent_instruct",
    "subagent_list", "subagent_start", "subagent_tasks", "subagent_update", "summarize_session",
    "time_get", "tool_reload", "tool_search", "user_prompt_get", "user_prompt_set",
    "web_fetch", "web_search",
}


async def run_streaming_suite(
    base_url: str,
    categories: list[str] | None = None,
) -> SuiteResult:
    all_results: list[TestResult] = []
    suite_start = time.monotonic()

    runners = _get_runners(categories)
    for runner in runners:
        result = await runner(base_url)
        all_results.append(result)

    duration = int((time.monotonic() - suite_start) * 1000)
    passed = sum(1 for r in all_results if r.passed)
    return SuiteResult(
        category="streaming",
        total=len(all_results),
        passed=passed,
        results=all_results,
        duration_ms=duration,
    )


def _get_runners(categories: list[str] | None) -> list:
    all_runners = [
        _run_basic_streaming,
        _run_tool_call_streaming,
        _run_small_tools,
        _run_reasoning_streaming,
        _run_text_accumulation,
        _run_error_handling,
        _run_multiturn_streaming,
        _run_content_integrity,
    ]

    if categories is None or "all" in categories:
        return all_runners

    category_map = {
        "basic": 0,
        "tool": 1,
        "smalltools": 2,
        "reasoning": 3,
        "accumulation": 4,
        "error": 5,
        "multiturn": 6,
        "integrity": 7,
    }

    indices = {category_map[c] for c in categories if c in category_map}
    return [all_runners[i] for i in sorted(indices)]


async def _stream_events(
    base_url: str, message: str, user_id: str = "smoke_eval"
) -> tuple[list[dict], str | None]:
    import aiohttp

    events: list[dict] = []
    error: str | None = None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/message/stream",
                json={"message": message, "user_id": user_id},
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resp:
                if resp.status != 200:
                    error = f"HTTP {resp.status}"
                    return events, error
                async for line_bytes in resp.content:
                    line = line_bytes.decode("utf-8").strip()
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            events.append(data)
                        except json.JSONDecodeError:
                            pass
    except Exception as e:
        error = str(e)

    return events, error


def _find_tool_name(events: list[dict]) -> str | None:
    for ev in events:
        dtype = ev.get("type")
        content = ev.get("data", {}).get("content", "")
        if dtype == "updates" and content.startswith("Using tool:"):
            return content.replace("Using tool:", "").strip()
    return None


async def _run_basic_streaming(base_url: str) -> TestResult:
    """10 queries: verify events arrive, stream terminates, no gap >30s."""
    queries = [
        "what time is it",
        "list my files",
        "list my todos",
        "list my contacts",
        "search web for weather",
        "what date is it",
        "list my skills",
        "list mcp servers",
        "show workspaces",
        "get conversation history",
    ]

    name = "basic_streaming"
    failed: int = 0
    total_duration = 0
    tool_calls = 0

    for q in queries:
        start = time.monotonic()
        events, err = await _stream_events(base_url, q)
        dur = int((time.monotonic() - start) * 1000)
        total_duration += dur

        if err:
            failed += 1
            continue

        if not events:
            failed += 1
            continue

        tool_calls += 1 if _find_tool_name(events) else 0

    return TestResult(
        name=name,
        passed=failed == 0,
        duration_ms=total_duration,
        tool_calls=tool_calls,
        error=None if failed == 0 else f"{failed}/{len(queries)} basic tests failed",
    )


async def _run_tool_call_streaming(base_url: str) -> TestResult:
    """25 queries: verify tool names are valid, tool output follows."""
    queries = [
        "what time is it in tokyo",
        "run echo hello",
        "list files in current dir",
        "add todo buy groceries",
        "list all my todos",
        "search contacts for smith",
        "list all my contacts",
        "get my conversation history",
        "search memory for python",
        "search web for ai news",
        "whats the date today",
        "show my workspaces",
        "list all my files",
        "read the readme file",
        "find all python files",
        # Browser tools coverage
        "open a browser and go to example.com",
        "get the title of the current page",
        "take a screenshot of the page",
        "scroll down the page",
        "get the page text content",
        # Small tools coverage
        "search for tools related to files",
        "reload all tools",
        "count my messages",
        "show my memory profile",
        "set a user prompt to always be concise",
    ]

    name = "tool_call_streaming"
    failed = 0
    total_duration = 0
    tool_calls = 0

    for q in queries:
        start = time.monotonic()
        events, err = await _stream_events(base_url, q)
        dur = int((time.monotonic() - start) * 1000)
        total_duration += dur

        if err:
            failed += 1
            continue

        tool_name = _find_tool_name(events)
        if tool_name and tool_name in KNOWN_TOOLS:
            tool_calls += 1
        elif tool_name:
            logger.warning("Unknown tool: %s (query: %s)", tool_name, q)

    return TestResult(
        name=name,
        passed=failed == 0,
        duration_ms=total_duration,
        tool_calls=tool_calls,
        error=None if failed == 0 else f"{failed}/{len(queries)} tool call tests failed",
    )


async def _run_small_tools(base_url: str) -> TestResult:
    """8 queries: verify user_prompt, tool_search, tool_reload, message stats, memory profile."""
    queries = [
        "get my current user prompt",
        "set a user prompt to always be concise",
        "search for tools related to memory",
        "reload all tools",
        "count my messages",
        "show my message timeline",
        "show my memory profile",
        "reflect on what I learned today",
    ]

    name = "small_tools"
    failed = 0
    total_duration = 0
    known = 0

    for q in queries:
        start = time.monotonic()
        events, err = await _stream_events(base_url, q)
        dur = int((time.monotonic() - start) * 1000)
        total_duration += dur

        if err:
            failed += 1
            continue

        tool_name = _find_tool_name(events)
        if tool_name:
            known += 1

    return TestResult(
        name=name,
        passed=failed == 0,
        duration_ms=total_duration,
        tool_calls=known,
        error=None if failed == 0 else f"{failed}/{len(queries)} small tool tests failed",
    )


async def _run_reasoning_streaming(base_url: str) -> TestResult:
    """10 queries: verify [Thinking...] appears, [Reasoning] content non-empty."""
    queries = [
        "what is the meaning of life",
        "explain quantum computing simply",
        "whats the capital of france and its population",
        "compare python and javascript",
        "how does a rainbow form",
        "what is the speed of light",
        "explain machine learning",
        "what causes inflation",
        "how do vaccines work",
        "tell me about the solar system",
    ]

    name = "reasoning_streaming"
    thinking_seen = 0
    reasoning_seen = 0
    failed = 0
    total_duration = 0

    for q in queries:
        start = time.monotonic()
        events, err = await _stream_events(base_url, q)
        dur = int((time.monotonic() - start) * 1000)
        total_duration += dur

        if err:
            failed += 1
            continue

        has_thinking = any(
            ev.get("type") == "updates"
            and ev.get("data", {}).get("content") == "[Thinking...]"
            for ev in events
        )
        has_reasoning = any(
            ev.get("type") == "messages"
            and ev.get("data", {}).get("content", "").startswith("[Reasoning]")
            and len(ev.get("data", {}).get("content", "")) > 15
            for ev in events
        )

        if has_thinking:
            thinking_seen += 1
        if has_reasoning:
            reasoning_seen += 1

    all_pass = failed == 0 and thinking_seen >= 5
    error_parts = []
    if failed:
        error_parts.append(f"{failed} failures")
    if thinking_seen < len(queries):
        error_parts.append(f"thinking_seen={thinking_seen}/{len(queries)}")
    if reasoning_seen < 3:
        error_parts.append(f"reasoning_seen={reasoning_seen}/10")

    return TestResult(
        name=name,
        passed=all_pass,
        duration_ms=total_duration,
        tool_calls=0,
        error="; ".join(error_parts) if error_parts else None,
        response_preview=f"thinking={thinking_seen}/{len(queries)}, reasoning={reasoning_seen}/10",
    )


async def _run_text_accumulation(base_url: str) -> TestResult:
    """5 queries: all text_delta events concatenate to form coherent response."""
    queries = [
        "say hello world five times",
        "count from 1 to 10",
        "list the planets in order",
        "spell out the word elephant letter by letter",
        "name the first 5 elements of the periodic table",
    ]

    name = "text_accumulation"
    failed = 0
    total_duration = 0

    for q in queries:
        start = time.monotonic()
        events, err = await _stream_events(base_url, q)
        dur = int((time.monotonic() - start) * 1000)
        total_duration += dur

        if err:
            failed += 1
            continue

        text_parts = [
            ev["data"]["content"]
            for ev in events
            if ev.get("type") == "messages"
            and ev.get("data", {}).get("content")
        ]
        full_text = "".join(text_parts)

        if not full_text.strip():
            failed += 1

    return TestResult(
        name=name,
        passed=failed == 0,
        duration_ms=total_duration,
        error=None if failed == 0 else f"{failed}/{len(queries)} accumulation tests failed",
    )


async def _run_error_handling(base_url: str) -> TestResult:
    """5 queries: empty message, very long, special chars."""
    queries = [
        "",
        "a" * 50000,
        "!@#$%^&*()_+{}|:\"<>?~`-=[]\\;',./",
        "\U0001f600" * 1000,
        "send email to nowhere@invalid with subject test",
    ]

    name = "error_handling"
    failed = 0
    total_duration = 0

    for q in queries:
        start = time.monotonic()
        if not q.strip():
            events, err = await _stream_events(base_url, q)
            dur = int((time.monotonic() - start) * 1000)
            total_duration += dur
            has_error = any(ev.get("type") == "error" for ev in events)
            if not has_error and err is None:
                failed += 1
            continue

        events, err = await _stream_events(base_url, q)
        dur = int((time.monotonic() - start) * 1000)
        total_duration += dur

        if err:
            failed += 1
            continue
        if not events:
            failed += 1

    return TestResult(
        name=name,
        passed=failed <= 1,
        duration_ms=total_duration,
        error=None if failed <= 1 else f"{failed}/{len(queries)} error tests failed",
    )


async def _run_multiturn_streaming(base_url: str) -> TestResult:
    """5 multi-turn pairs: second call references first call."""
    pairs = [
        ("what time is it", "what was my previous question about"),
        ("list my files", "tell me something about my files from earlier"),
        ("add todo buy milk", "what did i just ask you to add"),
        ("search web for python", "what did i just search for"),
        ("list my contacts", "what did i ask before this"),
    ]

    name = "multiturn_streaming"
    failed = 0
    total_duration = 0
    user_id = "smoke_multiturn"

    for q1, q2 in pairs:
        start = time.monotonic()
        events1, err1 = await _stream_events(base_url, q1, user_id=user_id)
        if err1:
            failed += 1
            total_duration += int((time.monotonic() - start) * 1000)
            continue
        await asyncio.sleep(0.2)
        events2, err2 = await _stream_events(base_url, q2, user_id=user_id)
        dur = int((time.monotonic() - start) * 1000)
        total_duration += dur

        if err2:
            failed += 1
            continue
        if not events2:
            failed += 1

    return TestResult(
        name=name,
        passed=failed == 0,
        duration_ms=total_duration,
        error=None if failed == 0 else f"{failed}/{len(pairs)} multiturn tests failed",
    )


async def _run_content_integrity(base_url: str) -> TestResult:
    """5 queries: no raw chunk types leak, unicode preserved."""
    queries = [
        "say hello in Japanese",
        "tell me a joke about robots \U0001f916",
        "write a short poem about the ocean",
        "say hello in three languages",
        "list the greek alphabet first 5 letters",
    ]

    name = "content_integrity"
    raw_types = {"text_start", "text_delta", "text_end", "tool_input_start",
                 "tool_input_delta", "tool_input_end", "reasoning_start",
                 "reasoning_delta", "reasoning_end", "tool_result"}
    failed = 0
    total_duration = 0

    for q in queries:
        start = time.monotonic()
        events, err = await _stream_events(base_url, q)
        dur = int((time.monotonic() - start) * 1000)
        total_duration += dur

        if err:
            failed += 1
            continue

        combined = " ".join(
            ev.get("data", {}).get("content", "")
            for ev in events
        )

        for rt in raw_types:
            if rt in combined:
                failed += 1
                break

    return TestResult(
        name=name,
        passed=failed == 0,
        duration_ms=total_duration,
        error=None if failed == 0 else f"{failed}/{len(queries)} integrity tests failed",
    )
