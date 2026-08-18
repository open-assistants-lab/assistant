#!/usr/bin/env python3
"""Langfuse trace inspector — list recent traces and dump one trace's tree.

Usage:
  uv run python scripts/langfuse_traces.py                 # list 10 recent traces
  uv run python scripts/langfuse_traces.py --limit 30      # more traces
  uv run python scripts/langfuse_traces.py --session unify-2   # find by session_id
  uv run python scripts/langfuse_traces.py --id <trace_id>     # full tree of one trace
  uv run python scripts/langfuse_traces.py --user grader_e2e   # filter by user
  uv run python scripts/langfuse_traces.py --tree --session <sid>  # tree for first match
  uv run python scripts/langfuse_traces.py --grader-check     # scan for grader activity
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

GENERATION = "GENERATION"
SPAN = "SPAN"


def _client() -> tuple[str, str]:
    v = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    host = (v.get("LANGFUSE_HOST") or "").rstrip("/")
    pk = v.get("LANGFUSE_PUBLIC_KEY") or ""
    sk = v.get("LANGFUSE_SECRET_KEY") or ""
    if not host or not pk or not sk:
        sys.exit("LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY missing in .env")
    return host, base64.b64encode(f"{pk}:{sk}".encode()).decode()


def get(host: str, auth: str, path: str) -> dict:
    req = urllib.request.Request(f"{host}{path}", headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def list_traces(host: str, auth: str, limit: int, session: str | None, user: str | None) -> None:
    traces = get(host, auth, f"/api/public/traces?limit={limit}")["data"]
    for t in traces:
        if session and t.get("sessionId") != session:
            continue
        if user and t.get("userId") != user:
            continue
        name = t.get("name") or "?"
        print(
            f"{t['id'][:12]}  {name:<28} user={str(t.get('userId')):<14} "
            f"session={str(t.get('sessionId')):<20} {(t.get('timestamp') or '')[:19]}"
        )


def dump_tree(host: str, auth: str, trace_id: str) -> None:
    detail = get(host, auth, f"/api/public/traces/{trace_id}")
    obs = {o["id"]: o for o in detail.get("observations", [])}
    print(f"TRACE {trace_id}  name={detail.get('name')}  user={detail.get('userId')}  "
          f"session={detail.get('sessionId')}")
    for o in detail.get("observations", []):
        parent = obs.get(o.get("parentObservationId")) if o.get("parentObservationId") else None
        name = (o.get("name") or "")[:52]
        model = (o.get("model") or "")[:28]
        level = o.get("level") or ""
        extra = f" model={model}" if model else ""
        err = f" LEVEL={level}" if level and level != "DEFAULT" else ""
        print(f"  {o.get('type'):10s} {name:<54s} parent={parent.get('name','ROOT') if parent else 'ROOT':<12s}{extra}{err}")


def grader_check(host: str, auth: str, limit: int) -> None:
    """Scan recent traces for grader activity and report a compact health summary."""
    traces = get(host, auth, f"/api/public/traces?limit={limit}")["data"]
    graded = 0
    reruns = 0
    issues: list[str] = []
    for t in traces:
        detail = get(host, auth, f"/api/public/traces/{t['id']}")
        obs = detail.get("observations", [])
        grade_spans = [o for o in obs if (o.get("name") or "") == "grader_run"]
        agent_runs = [o for o in obs if (o.get("name") or "") == "agent_run"]
        if grade_spans:
            graded += 1
            if len(agent_runs) > 1:
                reruns += 1
            for gs in grade_spans:
                for child in obs:
                    if child.get("parentObservationId") == gs["id"] and child.get("type") == GENERATION:
                        out = child.get("output") or {}
                        content = out.get("content", "") if isinstance(out, dict) else str(out)
                        if not content:
                            issues.append(f"{t.get('sessionId')}: grader returned EMPTY output")
                        else:
                            try:
                                verdict = json.loads(content)
                                issues.append(f"{t.get('sessionId')}: grader -> {verdict.get('result')}")
                            except Exception:
                                issues.append(f"{t.get('sessionId')}: grader output NOT JSON: {content[:60]!r}")
    print(f"scanned {len(traces)} traces | graded={graded} | with-rerun={reruns}")
    for issue in issues[:20]:
        print("  -", issue)


def main() -> None:
    ap = argparse.ArgumentParser(description="Langfuse trace inspector")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--session", default=None)
    ap.add_argument("--user", default=None)
    ap.add_argument("--id", default=None)
    ap.add_argument("--tree", action="store_true", help="dump the full tree of the first matching trace")
    ap.add_argument("--grader-check", action="store_true", help="scan recent traces for grader health")
    args = ap.parse_args()

    host, auth = _client()
    if args.id:
        dump_tree(host, auth, args.id)
    elif args.grader_check:
        grader_check(host, auth, args.limit)
    else:
        list_traces(host, auth, args.limit, args.session, args.user)
        if args.tree:
            traces = get(host, auth, f"/api/public/traces?limit={args.limit}")["data"]
            match = next(
                (t for t in traces
                 if (not args.session or t.get("sessionId") == args.session)
                 and (not args.user or t.get("userId") == args.user)),
                None,
            )
            if match:
                print()
                dump_tree(host, auth, match["id"])


if __name__ == "__main__":
    main()
