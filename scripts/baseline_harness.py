#!/usr/bin/env python3
"""Harness latency baseline runner (E spec).

Starts an isolated server, runs N mixed turns, reads the harness.waterfall
JSONL lines, and reports per-stage p50/p95/p99. Writes the baseline table
to docs/BASELINE_HARNESS_<date>.md.

Usage:
    uv run python scripts/baseline_harness.py [--turns 40] [--model ollama-cloud:deepseek-v4-flash:0731]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORT = 8899
BASE = f"http://127.0.0.1:{PORT}"

PLAIN = [
    "Say hello in one short sentence.",
    "What is 2+2? Answer with just the number.",
    "Name three colors. Short list.",
    "What is the capital of Japan? One word.",
    "Translate 'good morning' to Spanish. Short.",
    "What is the tallest mountain? One sentence.",
    "Is water wet? One sentence.",
    "What is the opposite of up? One word.",
]

TOOLS = [
    "What time is it? Use the time tool.",
    "Count how many messages we've exchanged in this session.",
    "What files exist in the workspace? List them.",
    "Use the time tool twice and tell me both results.",
    "Read the file src/sdk/messages.py and tell me what the Message class has.",
]


def _call(text: str, user: str, session: str, verify_mode: str | None) -> float:
    payload = {"message": text, "model": MODEL, "user_id": user, "session_id": session}
    if verify_mode:
        payload["verification"] = {"mode": verify_mode}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE}/message", data=body, headers={"Content-Type": "application/json"}
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=300) as resp:
        json.loads(resp.read())
    return time.monotonic() - t0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * p
    f = int(k)
    c = min(f + 1, len(values) - 1)
    return values[f] + (values[c] - values[f]) * (k - f)


def main() -> int:
    global MODEL
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=40)
    parser.add_argument("--model", default="ollama-cloud:deepseek-v4-flash:0731")
    parser.add_argument("--verify-mode", default=None, choices=["off", "on", "auto"])
    args = parser.parse_args()
    MODEL = args.model
    verify_mode = args.verify_mode

    tmp = Path(tempfile.mkdtemp(prefix="harness_baseline_"))
    env = {
        **os.environ,
        "DEPLOYMENT_DATA_ROOT": str(tmp / "data_root"),
        "DEPLOYMENT_DATA_PATH": str(tmp / "data"),
    }
    # NB: config.yaml init-kwargs beat env vars in pydantic-settings, so the
    # logger's json_dir stays config.yaml's "data/logs" relative to the
    # server CWD (= REPO). The script reads waterfalls from there.
    log_root = REPO / "data" / "logs"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.http.main:app", "--port", str(PORT)],
        cwd=REPO,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # NB: the app logger timestamps use local time with the zone offset;
    # compare in the same convention (naive local) for string ordering.
    started_at = datetime.now().isoformat()
    try:
        for _ in range(30):
            time.sleep(2)
            try:
                urllib.request.urlopen(f"{BASE}/health", timeout=2)
                break
            except Exception:
                continue
        else:
            print("server failed to start", file=sys.stderr)
            return 1

        print(
            f"running {args.turns} turns (model={MODEL}, verify_mode={verify_mode})",
            flush=True,
        )
        latencies: list[float] = []  # seconds
        for i in range(args.turns):
            text = (TOOLS if i % 3 == 0 else PLAIN)[i % len(TOOLS if i % 3 == 0 else PLAIN)]
            lat = _call(text, f"baseline_u{i % 3}", f"sess_{i % 3}", verify_mode)
            latencies.append(lat * 1000.0)  # ms
            if (i + 1) % 10 == 0:
                print(f"  [{i + 1}/{args.turns}] last={lat:.1f}s", flush=True)

        time.sleep(2)  # let the logger flush

        waterfalls: list[dict] = []
        for f in log_root.glob(f"{time.strftime('%Y-%m-%d')}*.jsonl"):
            for line in f.read_text().splitlines():
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("event") != "harness.waterfall":
                    continue
                ts = d.get("timestamp", "")
                if ts < started_at:
                    continue  # only this run's turns
                waterfalls.append(d.get("data", {}))

        if not waterfalls:
            print("no harness.waterfall lines found", file=sys.stderr)
            return 1

        stages = sorted(
            {s for w in waterfalls for s in w.get("stages_ms", {}) if not s.endswith("_count")}
        )
        rows: list[tuple[str, float, float, float, float]] = []
        for stage in stages:
            vals = [w["stages_ms"][stage] for w in waterfalls if stage in w.get("stages_ms", {})]
            rows.append((stage, _percentile(vals, 0.5), _percentile(vals, 0.95), _percentile(vals, 0.99), max(vals)))
        totals = [w.get("total_ms", 0) for w in waterfalls]
        rows.append(("total (waterfall)", _percentile(totals, 0.5), _percentile(totals, 0.95), _percentile(totals, 0.99), max(totals)))
        rows.append(("turn (client-side)", _percentile(latencies, 0.5), _percentile(latencies, 0.95), _percentile(latencies, 0.99), max(latencies)))

        print("\n=== BASELINE ===")
        print(f"{'stage':<22}{'p50':>8}{'p95':>8}{'p99':>8}{'max':>8}")
        for name, p50, p95, p99, mx in rows:
            print(f"{name:<22}{p50:>8.0f}{p95:>8.0f}{p99:>8.0f}{mx:>8.0f}")

        date = datetime.now(UTC).strftime("%Y-%m-%d")
        doc = REPO / f"docs/BASELINE_HARNESS_{date}.md"
        lines = [
            "# Harness Latency Baseline",
            "",
            f"**Date:** {datetime.now(UTC).isoformat()}",
            f"**Model:** {MODEL}",
            f"**Verify mode:** {verify_mode or 'settings default'}",
            f"**Turns:** {args.turns} (mixed plain + tool-using)",
            f"**Runs with waterfall lines:** {len(waterfalls)}",
            "",
            "All values in milliseconds (lower is better).",
            "",
            "| Stage | p50 | p95 | p99 | max |",
            "|-------|-----|-----|-----|-----|",
        ]
        for name, p50, p95, p99, mx in rows:
            lines.append(f"| {name} | {p50:.0f} | {p95:.0f} | {p99:.0f} | {mx:.0f} |")
        lines.append("")
        doc.write_text("\n".join(lines))
        print(f"\nbaseline written to {doc}")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
