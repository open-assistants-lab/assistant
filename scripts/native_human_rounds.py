#!/usr/bin/env python3
"""Drive N natural chat rounds through the native SDK app.

Messages are human-to-human style (greetings, small talk, questions,
requests) rather than strict constraints — the grader's default rubric
('- Response is non-empty') should mostly pass, so reruns are rare.

Usage:
  uv run python scripts/native_human_rounds.py --rounds 50
"""

from __future__ import annotations

import argparse
import re
import subprocess
import time

APP_DIR = "/Users/eddy/Developer/Python/assistant/native-sdk-experiment"

MESSAGES = [
    "hey there",
    "good morning! how are you?",
    "what can you actually do?",
    "can you help me with my math homework?",
    "what's 17 times 23?",
    "hmm that seems right, can you double check?",
    "write me a haiku about coffee",
    "that's nice, do another one about rain",
    "what's the capital of australia?",
    "tell me something interesting about sydney",
    "can you give me a simple pasta recipe?",
    "how long does it take to cook?",
    "what's the weather usually like in melbourne?",
    "do you know any good books to read?",
    "i like sci-fi, any recommendations?",
    "what's your favorite movie?",
    "can you explain how the internet works?",
    "simplify that for me, i'm not technical",
    "what's the difference between ai and ml?",
    "are you an ai?",
    "who made you?",
    "can you tell me a joke?",
    "that was funny, tell another one",
    "what time is it?",
    "can you set a reminder for me?",
    "actually never mind, forget the reminder",
    "what's 15% of 80?",
    "convert 100 km to miles",
    "what's the square root of 144?",
    "can you write a short story about a cat?",
    "make it a bit longer please",
    "what's your favorite color?",
    "do you have opinions?",
    "what should i eat for dinner tonight?",
    "i'm feeling tired, any advice?",
    "how do i get better at sleeping?",
    "what's a good morning routine?",
    "can you plan my day for me?",
    "what should i focus on first?",
    "thanks, that's helpful",
    "what's the best way to learn python?",
    "how long does it take to learn?",
    "any project ideas for beginners?",
    "what's a good first project?",
    "can you review my code?",
    "here's my code: print('hello')",
    "is that correct?",
    "what does the print function do?",
    "thanks for all your help today",
    "see you later!",
]


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
    ap.add_argument("--launch", action="store_true")
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
        for _ in range(60):
            if widget_id(snap(), "textbox", "Message") is not None:
                break
            time.sleep(0.5)
        time.sleep(2)

    s = snap()
    new_chat = widget_id(s, "button", "New chat")
    if new_chat:
        click(new_chat)
        time.sleep(1.5)

    results: list[dict] = []
    for i in range(args.start_round, args.rounds + 1):
        msg = MESSAGES[(i - 1) % len(MESSAGES)]
        s = snap()
        tb = widget_id(s, "textbox", "Message")
        if tb is None:
            print(f"round {i}: textbox not found — aborting", flush=True)
            break
        focus(tb)
        type_text(msg)
        s = snap()
        send = widget_id(s, "button", "Send")
        if send is None:
            print(f"round {i}: Send not found — aborting", flush=True)
            break
        t0 = time.monotonic()
        click(send)
        ok = wait_send_button()
        elapsed = time.monotonic() - t0
        results.append({"round": i, "ok": ok, "elapsed": round(elapsed, 1), "msg": msg})
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
