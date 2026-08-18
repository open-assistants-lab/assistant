#!/usr/bin/env python3
"""Run N chat rounds against the local backend with rubric verification.

The grader triggers via config (VERIFICATION_ENABLED=true + default rubric)
— messages are sent WITHOUT an explicit verification field, exercising the
production config-gate path. Rounds are spread across a few sessions and
run concurrently (per-session locks allow parallel sessions).

Usage:
  uv run python scripts/stress_50_rounds.py --rounds 50 --workers 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from collections import Counter

BASE = "http://127.0.0.1:8080"
USER = "stress50"


def post_message(session_id: str, message: str) -> dict:
    body = json.dumps({
        "message": message,
        "user_id": USER,
        "session_id": session_id,
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/message", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


async def worker(worker_id: int, rounds: int, results: list[dict]) -> None:
    for i in range(1, rounds + 1):
        n = worker_id * 1000 + i
        session = f"stress-{worker_id}-{i}"
        start = time.monotonic()
        try:
            resp = await asyncio.to_thread(
                post_message, session, f"Reply exactly: round {n}"
            )
            v = resp.get("verification") or {}
            results.append({
                "worker": worker_id,
                "session": session,
                "round": n,
                "ok": bool(resp.get("response")),
                "response_len": len(resp.get("response") or ""),
                "vstatus": v.get("status"),
                "vattempts": v.get("attempts", 0),
                "elapsed": round(time.monotonic() - start, 1),
            })
            print(f"  w{worker_id} r{i:02d} -> {v.get('status')} "
                  f"(attempts={v.get('attempts', 0)}) {time.monotonic()-start:.1f}s",
                  flush=True)
        except Exception as exc:
            results.append({
                "worker": worker_id, "session": session, "round": n,
                "ok": False, "error": str(exc)[:120],
                "elapsed": round(time.monotonic() - start, 1),
            })
            print(f"  w{worker_id} r{i:02d} -> ERROR {exc}", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=50)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    per_worker = args.rounds // args.workers
    extra = args.rounds % args.workers
    results: list[dict] = []
    print(f"Starting {args.rounds} rounds across {args.workers} workers "
          f"({per_worker}+ each)...")
    tasks = [
        asyncio.create_task(worker(w, per_worker + (1 if w < extra else 0), results))
        for w in range(args.workers)
    ]
    await asyncio.gather(*tasks)

    ok = sum(1 for r in results if r.get("ok"))
    failed = [r for r in results if not r.get("ok")]
    statuses = Counter(r.get("vstatus") for r in results if r.get("ok"))
    attempts = Counter(r.get("vattempts") for r in results if r.get("ok"))
    total_time = round(sum(r.get("elapsed", 0) for r in results), 1)
    avg = round(total_time / max(len(results), 1), 1)

    print("\n=== SUMMARY ===")
    print(f"rounds: {len(results)} | ok: {ok} | failed: {len(failed)}")
    print(f"verification statuses: {dict(statuses)}")
    print(f"attempt counts: {dict(attempts)}")
    print(f"total wall time: {total_time}s | avg per round: {avg}s")
    if failed:
        print("failures:")
        for f in failed[:10]:
            print("  -", f)
    print("\nRESULT_JSON=" + json.dumps({
        "rounds": len(results), "ok": ok, "failed": len(failed),
        "statuses": dict(statuses), "attempts": dict(attempts),
        "avg_seconds": avg,
    }))


if __name__ == "__main__":
    asyncio.run(main())
