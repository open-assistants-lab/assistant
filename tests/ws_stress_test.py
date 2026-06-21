#!/usr/bin/env python3
"""WebSocket stress test: 100 sequential interactions."""
import asyncio
import json
import sys
import time

import websockets

URI = "ws://localhost:8080/ws/conversation?user_id=default_user"

MESSAGES = [
    "What is 2+2?",
    "What time is it in UTC?",
    "Say hello in one word.",
    "What is the capital of France?",
    "Count from 1 to 5.",
    "What color is the sky?",
    "Name a fruit.",
    "What day is tomorrow?",
    "Reply: yes or no. Is water wet?",
    "What is 10 minus 3?",
]


async def single_interaction(ws, msg: str, idx: int) -> tuple[int, bool, float, str]:
    start = time.monotonic()
    try:
        await ws.send(json.dumps({"type": "user_message", "content": msg}))
        response_parts = []
        while True:
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=60)
                data = json.loads(resp)
                t = data.get("type", "?")
                if t == "text_delta":
                    response_parts.append(data.get("text", ""))
                elif t == "error":
                    err = data.get("message") or data.get("detail") or data.get("error") or ""
                    elapsed = time.monotonic() - start
                    return (idx, False, elapsed, f"ERROR: {err}")
                elif t == "done":
                    elapsed = time.monotonic() - start
                    text = "".join(response_parts).strip()
                    truncated = text[:80] + ("..." if len(text) > 80 else "")
                    return (idx, True, elapsed, truncated)
            except TimeoutError:
                elapsed = time.monotonic() - start
                return (idx, False, elapsed, "TIMEOUT")
    except Exception as e:
        elapsed = time.monotonic() - start
        return (idx, False, elapsed, str(e))


async def main():
    total = 100
    results: list[tuple] = []
    ok = 0
    fail = 0
    start_time = time.monotonic()

    print(f"WebSocket stress test: {total} interactions", flush=True)
    print(f"URI: {URI}", flush=True)
    print("-" * 60, flush=True)

    async with websockets.connect(URI) as ws:
        for i in range(total):
            msg = MESSAGES[i % len(MESSAGES)]
            idx, success, elapsed, response = await single_interaction(ws, msg, i)
            results.append((idx, success, elapsed, response))
            if success:
                ok += 1
            else:
                fail += 1
            status = "OK" if success else "FAIL"
            print(f"[{i+1:03d}/{total}] {status} {elapsed:.1f}s | {msg[:40]:40s} → {response}", flush=True)

    elapsed_total = time.monotonic() - start_time
    avg = sum(r[2] for r in results) / len(results) if results else 0
    times = sorted(r[2] for r in results)
    p50 = times[len(times) // 2]
    p95 = times[int(len(times) * 0.95)]

    print("-" * 60, flush=True)
    print(f"Total: {elapsed_total:.1f}s | OK: {ok} | FAIL: {fail}", flush=True)
    print(f"Avg: {avg:.1f}s | P50: {p50:.1f}s | P95: {p95:.1f}s", flush=True)
    print(f"Min: {times[0]:.1f}s | Max: {times[-1]:.1f}s", flush=True)

    if fail:
        print("\nFailures:", flush=True)
        for idx, _, elapsed, response in results:
            if not (results[idx][1]):
                print(f"  [{idx+1}] {response}", flush=True)

    return fail == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
