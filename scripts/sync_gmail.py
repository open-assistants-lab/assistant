#!/usr/bin/env python3
"""Sync Gmail emails into HybridDB cache.

Usage:
    python3 scripts/sync_gmail.py                    # sync last 50 inbox emails
    python3 scripts/sync_gmail.py --max 100           # sync last 100
    python3 scripts/sync_gmail.py --max 200 --query "from:boss"
    python3 scripts/sync_gmail.py --user aderts       # specific user
    python3 scripts/sync_gmail.py --no-body           # skip body (faster, metadata only)
    python3 scripts/sync_gmail.py --search "invoice"  # search cached emails

Requires: gws CLI installed and authenticated.
"""

import argparse
import sys
from pathlib import Path

# Ensure the project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from datetime import UTC

from src.storage.gmail_cache import get_gmail_cache, sync_emails


def main():
    parser = argparse.ArgumentParser(description="Sync Gmail to HybridDB cache")
    parser.add_argument("--max", type=int, default=50, help="Max emails to sync (default: 50, use 0 for unlimited)")
    parser.add_argument("--query", type=str, help="Gmail search query (e.g. 'from:boss', 'is:unread')")
    parser.add_argument("--after", type=str, help="Sync emails after this date (YYYY-MM-DD)")
    parser.add_argument("--before", type=str, help="Sync emails before this date (YYYY-MM-DD)")
    parser.add_argument("--user", type=str, default="default_user", help="User ID")
    parser.add_argument("--no-body", action="store_true", help="Skip body fetching (metadata only)")
    parser.add_argument("--search", type=str, help="Search cached emails instead of syncing")
    parser.add_argument("--stats", action="store_true", help="Show cache stats")
    parser.add_argument("--recent", type=int, help="Show N most recent cached emails")
    parser.add_argument("--clear", action="store_true", help="Clear all cached emails")
    args = parser.parse_args()

    cache = get_gmail_cache(args.user)

    # -- Read operations --

    if args.stats:
        s = cache.stats()
        print(f"Total emails: {s['total']}")
        print(f"Journal pending: {s['journal']}")
        print(f"Health: {s['health']}")
        return

    if args.recent:
        emails = cache.get_recent(args.recent)
        _print_emails(emails)
        return

    if args.search:
        emails = cache.search_hybrid(args.search, limit=10)
        print(f"\nSearch results for: {args.search}")
        print("-" * 80)
        _print_emails(emails)
        return

    if args.clear:
        cache.clear()
        print("Cache cleared.")
        return

    # -- Sync operation --

    # Build query from date range args
    query_parts = []
    if args.after:
        query_parts.append(f"after:{args.after}")
    if args.before:
        query_parts.append(f"before:{args.before}")
    if args.query:
        query_parts.append(args.query)

    gmail_query = " ".join(query_parts) if query_parts else None
    if gmail_query:
        print(f"Query: {gmail_query}")

    fetch_body = not args.no_body
    max_results = args.max if args.max > 0 else 1000000

    result = sync_emails(
        user_id=args.user,
        max_results=max_results,
        query=gmail_query,
        fetch_body=fetch_body,
    )

    print(f"Listed: {result['listed']} | Fetched: {result['fetched']} | Upserted: {result['upserted']} | Errors: {result['errors']}")
    print(f"Total in cache: {cache.count()}")


def _print_emails(emails):
    for e in emails:
        date_str = _fmt_ts(e.ts) if e.ts else "?"
        print(f"[{date_str}] {e.from_addr:<30} | {e.subject[:60]}")
        if e._score > 0:
            print(f"  score={e._score:.3f}  id={e.message_id}")
        else:
            print(f"  id={e.message_id}")


def _fmt_ts(ts: int) -> str:
    from datetime import datetime

    try:
        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


if __name__ == "__main__":
    main()
