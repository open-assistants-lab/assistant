"""Closed local SQLite fixture. Stable IDs and revision checks, not production Zii."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def initialise_state(db_path: Path, domain_path: Path) -> None:
    stores = json.loads(domain_path.read_text())["stores"]
    if [s["id"] for s in stores] != ["fixture-alpha", "fixture-beta"]:
        raise ValueError("invalid_domain")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS stores (id TEXT PRIMARY KEY, slug TEXT NOT NULL, "
                     "paused INTEGER NOT NULL CHECK(paused IN (0,1)), revision INTEGER NOT NULL)")
        conn.executemany("INSERT OR IGNORE INTO stores VALUES (?, ?, ?, ?)",
                         [(s["id"], s["slug"], int(s["paused"]), s["revision"]) for s in stores])


def _result(row: tuple[str, str, int, int], outcome: str) -> dict[str, object]:
    return {"ok": True, "outcome": outcome, "store_id": row[0], "paused": bool(row[2]), "revision": row[3]}


def _connection(db_path: Path) -> sqlite3.Connection:
    # mode=rw prevents typo paths becoming silently created databases.
    return sqlite3.connect(db_path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5)


def read_store(db_path: Path, store_id: str) -> dict[str, object]:
    conn = _connection(db_path)
    try:
        row = conn.execute("SELECT id, slug, paused, revision FROM stores WHERE id=?", (store_id,)).fetchone()
        if row is None:
            raise ValueError("unknown_target")
        return _result(row, "read")
    finally:
        conn.close()


def set_pause(db_path: Path, store_id: str, paused: bool, expected_revision: int) -> dict[str, object]:
    if type(paused) is not bool:
        raise ValueError("invalid_pause")
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("invalid_revision")
    conn = _connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id, slug, paused, revision FROM stores WHERE id=?", (store_id,)).fetchone()
        if row is None:
            raise ValueError("unknown_target")
        if row[3] != expected_revision:
            raise ValueError("stale_revision")
        if bool(row[2]) == paused:
            conn.commit()
            return _result(row, "noop")
        conn.execute("UPDATE stores SET paused=?, revision=revision+1 WHERE id=? AND revision=?",
                     (int(paused), store_id, expected_revision))
        conn.commit()
        return _result((store_id, row[1], int(paused), expected_revision + 1), "changed")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init")
    for name in ("read", "pause"):
        p = sub.add_parser(name)
        p.add_argument("--store-id", required=True)
        if name == "pause":
            p.add_argument("--paused", choices=("true", "false"), required=True)
            p.add_argument("--expected-revision", type=int, required=True)
    args = parser.parse_args()
    try:
        if args.action == "init":
            initialise_state(args.state, Path(__file__).with_name("domain.json"))
            result = {"ok": True, "outcome": "initialised"}
        elif args.action == "read":
            result = read_store(args.state, args.store_id)
        else:
            result = set_pause(args.state, args.store_id, args.paused == "true", args.expected_revision)
        print(json.dumps(result))
        return 0
    except (ValueError, sqlite3.Error, OSError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "state_unavailable"
        print(json.dumps({"ok": False, "error": reason}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
