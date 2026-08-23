#!/usr/bin/env python3
"""Gmail real-time sync daemon for HybridDB cache.

Runs gws +watch as a persistent subprocess. Gmail pushes notifications to
Pub/Sub when new mail arrives; gws pulls them and streams NDJSON to stdout;
this daemon reads that stream and upserts into HybridDB instantly.

Usage:
  python3 scripts/gmail_daemon.py --watch --project gws-cli-20260428
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, "reconfigure") else None

from src.storage.gmail_cache import (
    _extract_attachments,
    _extract_body,
    _parse_address_list,
    _parse_date_to_ts,
    get_gmail_cache,
)


def _now() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")


def msg_to_cache(msg: dict) -> dict | None:
    """Convert gws +watch NDJSON message into cache-ready dict."""
    msg_id = msg.get("id")
    if not msg_id:
        return None

    payload = msg.get("payload", {})
    headers = {h["name"]: h["value"] for h in payload.get("headers", [])}

    date_str = headers.get("Date", "")
    ts = _parse_date_to_ts(date_str)

    to_raw = headers.get("To", "")
    to_list = _parse_address_list(to_raw) if to_raw else []

    body = _extract_body(payload) if payload else ""
    attachments = _extract_attachments(payload) if payload else []

    important = {}
    for k in ["List-Unsubscribe", "List-Unsubscribe-Post", "Message-ID", "In-Reply-To", "References"]:
        v = headers.get(k, "")
        if v:
            important[k] = v

    return {
        "message_id": msg_id,
        "thread_id": msg.get("threadId", msg_id),
        "from_addr": headers.get("From", ""),
        "to_addr": to_list,
        "subject": headers.get("Subject", "(no subject)"),
        "snippet": msg.get("snippet", ""),
        "body": body,
        "ts": ts,
        "labels": msg.get("labelIds", []),
        "headers": important,
        "attachments": attachments,
    }


def watch_mode():
    """Run gws +watch as persistent subprocess. Handles full lifecycle."""
    cache = get_gmail_cache()
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    project = os.environ.get("GWS_PROJECT", "gws-cli-20260428")
    labels = os.environ.get("GWS_WATCH_LABELS", "INBOX")

    print(f"[{_now()}] Watch daemon starting (project={project}, labels={labels})")

    while running:
        try:
            cmd = [
                "gws", "gmail", "+watch",
                "--project", project,
                "--label-ids", labels,
                "--msg-format", "full",
                "--format", "json",
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            print(f"[{_now()}] gws +watch PID={proc.pid} — waiting for email...")

            synced = 0

            for line in proc.stdout:
                if not running:
                    proc.terminate()
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                    if "id" in msg and "threadId" in msg:
                        email = msg_to_cache(msg)
                        if email:
                            cache.upsert(email)
                            synced += 1
                            subj = email.get("subject", "")[:50]
                            print(f"[{_now()}] New: {subj}")

                            if synced % 10 == 0:
                                cache.db.process_journal(limit=10000)
                except json.JSONDecodeError:
                    pass

            proc.wait(timeout=5)

            if synced:
                cache.db.process_journal(limit=10000)
                print(f"[{_now()}] Session: {synced} new emails. Cache: {cache.count()}")

        except Exception as e:
            print(f"[{_now()}] Error: {e}")

        if running:
            print(f"[{_now()}] Reconnecting in 5s...")
            time.sleep(5)

    print(f"[{_now()}] Watch daemon stopped.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Gmail real-time sync daemon")
    parser.add_argument("--watch", action="store_true", help="Watch mode (Pub/Sub push)")
    args = parser.parse_args()

    if args.watch:
        watch_mode()
    else:
        print("Use --watch for real-time Pub/Sub push mode.")


if __name__ == "__main__":
    main()
