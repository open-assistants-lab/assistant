#!/usr/bin/env python3
"""Drive N chat rounds through the native SDK app via `native automate`.

Each round: type a message, click Send, wait for the Send button to
re-enable (streaming + verification done), record elapsed time. The
backend writes Langfuse traces for every round; capture them afterwards
with the langfuse CLI.

Usage:
  uv run python scripts/native_stress_rounds.py --rounds 50
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time

APP_DIR = "/Users/eddy/Developer/Python/assistant/native-sdk-experiment"


def run(cmd: list[str]) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=APP_DIR, timeout=30)
    return out.stdout


def snap() -> str:
    return run(["native", "automate", "snapshot"])


def widget_id(s: str, role: str, name: str) -> str | None:
    m = re.search(rf'widget @w1/main-canvas#(\d+) role={role} name="{re.escape(name)}"', s)
    return m.group(1) if m else None


def click(wid: str) -> None:
    run(["native", "automate", "widget-click", "main-canvas", wid])


def focus(wid: str) -> None:
    run(["native", "automate", "widget-action", "main-canvas", wid, "focus"])


def type_text(text: str) -> None:
    run(["native", "automate", "widget-key", "main-canvas", "a", text])


def wait_send_button(timeout_s: float = 150.0) -> bool:
    """Wait until the Send button is pressable again (streaming done)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        s = snap()
        if widget_id(s, "button", "Send") is not None:
            return True
        time.sleep(0.5)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=50)
    ap.add_argument("--start-round", type=int, default=1)
    ap.add_argument("--launch", action="store_true", help="launch the app first")
    args = ap.parse_args()

    if args.launch:
        subprocess.run(["pkill", "-f", r"\.native/build/.*/assistant"], capture_output=True)
        time.sleep(1)
        subprocess.run(["rm", "-rf", ".zig-cache/native-sdk-automation"], cwd=APP_DIR)
        subprocess.Popen(
            ["native", "dev", "-Dautomation=true"],
            cwd=APP_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # wait for UI ready
        for _ in range(60):
            if widget_id(snap(), "textbox", "Message") is not None:
                break
            time.sleep(0.5)
        time.sleep(2)

    # New chat so the run starts clean
    s = snap()
    new_chat = widget_id(s, "button", "New chat")
    if new_chat:
        click(new_chat)
        time.sleep(1.5)

    results: list[dict] = []
    for i in range(args.start_round, args.rounds + 1):
        s = snap()
        tb = widget_id(s, "textbox", "Message")
        if tb is None:
            print(f"round {i}: textbox not found — aborting", flush=True)
            break
        focus(tb)
        type_text(f"Reply exactly: round {i}")
        s = snap()
        send = widget_id(s, "button", "Send")
        if send is None:
            print(f"round {i}: Send not found — aborting", flush=True)
            break
        t0 = time.monotonic()
        click(send)
        ok = wait_send_button()
        elapsed = time.monotonic() - t0
        results.append({"round": i, "ok": ok, "elapsed": round(elapsed, 1)})
        if i % 5 == 0 or not ok:
            print(
                f"round {i}/{args.rounds} ok={ok} elapsed={elapsed:.1f}s "
                f"avg={sum(r['elapsed'] for r in results)/len(results):.1f}s",
                flush=True,
            )

    ok_count = sum(1 for r in results if r["ok"])
    total = sum(r["elapsed"] for r in results)
    print(
        f"\nDONE: {ok_count}/{len(results)} rounds completed, "
        f"total {total:.0f}s, avg {total/max(len(results),1):.1f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
