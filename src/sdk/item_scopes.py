"""ItemScopeDB — per-item scope storage (All / Selected / None) for tools, skills, subagents."""

from __future__ import annotations

import json
import sqlite3
from builtins import set as _set
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ScopeKind = Literal["all", "selected", "none"]


@dataclass
class ItemScope:
    resource_type: str  # "tool", "skill", "subagent"
    resource_name: str
    scope: ScopeKind
    workspace_ids: list[str] = field(default_factory=list)


class ItemScopeDB:
    """Per-user SQLite store for resource scope configuration.

    Usage:
        db = ItemScopeDB("data/users/alice")
        db.set("alice", "tool", "shell_execute", "selected", ["ws-1", "ws-2"])
        names = db.get_available_names("alice", "tool", "ws-1")
    """

    def __init__(self, data_dir: str | Path):
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def _db_path(self) -> Path:
        return self._dir / "item_scopes.db"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS item_scopes (
                user_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_name TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'all',
                workspace_ids TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (user_id, resource_type, resource_name)
            )
        """)
        return conn

    # ── single-item CRUD ──────────────────────────────────────────

    def get(
        self, user_id: str, resource_type: str, resource_name: str
    ) -> ItemScope | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM item_scopes "
                "WHERE user_id=? AND resource_type=? AND resource_name=?",
                (user_id, resource_type, resource_name),
            ).fetchone()
        if not row:
            return None
        return ItemScope(
            resource_type=row["resource_type"],
            resource_name=row["resource_name"],
            scope=row["scope"],
            workspace_ids=json.loads(row["workspace_ids"]),
        )

    def set(
        self,
        user_id: str,
        resource_type: str,
        resource_name: str,
        scope: ScopeKind,
        workspace_ids: list[str] | None = None,
    ) -> None:
        wids = json.dumps(workspace_ids or [])
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO item_scopes
                   (user_id, resource_type, resource_name, scope, workspace_ids)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, resource_type, resource_name) DO UPDATE SET
                   scope=excluded.scope, workspace_ids=excluded.workspace_ids""",
                (user_id, resource_type, resource_name, scope, wids),
            )

    def delete(
        self, user_id: str, resource_type: str, resource_name: str
    ) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM item_scopes "
                "WHERE user_id=? AND resource_type=? AND resource_name=?",
                (user_id, resource_type, resource_name),
            )
        return cur.rowcount > 0

    # ── multi-item queries ────────────────────────────────────────

    def list_all_for_type(
        self, user_id: str, resource_type: str
    ) -> list[ItemScope]:
        """Return all scope records for a resource type."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM item_scopes WHERE user_id=? AND resource_type=?",
                (user_id, resource_type),
            ).fetchall()
        return [
            ItemScope(
                resource_type=r["resource_type"],
                resource_name=r["resource_name"],
                scope=r["scope"],
                workspace_ids=json.loads(r["workspace_ids"]),
            )
            for r in rows
        ]

    def get_available_names(
        self, user_id: str, resource_type: str, workspace_id: str
    ) -> _set[str]:
        """Return resource names available for a specific workspace.

        Includes:
        - scope=all items
        - scope=selected items where workspace_id is in the list

        Excludes:
        - scope=none items
        - scope=selected items without the workspace

        Items NOT in the table default to scope=all (SDK/registry default)
        and are therefore available everywhere. The caller is responsible
        for merging this set with the full list of known resources.
        """
        names: set[str] = set()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT resource_name, scope, workspace_ids FROM item_scopes
                   WHERE user_id=? AND resource_type=? AND scope != 'none'""",
                (user_id, resource_type),
            ).fetchall()
        for r in rows:
            if r["scope"] == "all":
                names.add(r["resource_name"])
            elif r["scope"] == "selected":
                wids = json.loads(r["workspace_ids"])
                if workspace_id in wids:
                    names.add(r["resource_name"])
        return names

    def get_excluded_names(
        self, user_id: str, resource_type: str
    ) -> _set[str]:
        """Return resource names explicitly set to scope=none."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT resource_name FROM item_scopes "
                "WHERE user_id=? AND resource_type=? AND scope='none'",
                (user_id, resource_type),
            ).fetchall()
        return {r["resource_name"] for r in rows}

    def get_all_scoped(
        self, user_id: str, resource_type: str
    ) -> dict[str, ItemScope]:
        """Return {resource_name: ItemScope} for all configured items."""
        records = self.list_all_for_type(user_id, resource_type)
        return {r.resource_name: r for r in records}

    # ── workspace cleanup ─────────────────────────────────────────

    def remove_workspace(self, user_id: str, workspace_id: str) -> int:
        """Remove a workspace from all selected scopes. Returns count of rows changed."""
        changed = 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT resource_type, resource_name, workspace_ids FROM item_scopes "
                "WHERE user_id=? AND scope='selected'",
                (user_id,),
            ).fetchall()
            for r in rows:
                wids: list[str] = json.loads(r["workspace_ids"])
                if workspace_id in wids:
                    wids.remove(workspace_id)
                    new_scope: ScopeKind = "selected" if wids else "none"
                    conn.execute(
                        "UPDATE item_scopes SET scope=?, workspace_ids=? "
                        "WHERE user_id=? AND resource_type=? AND resource_name=?",
                        (new_scope, json.dumps(wids), user_id,
                         r["resource_type"], r["resource_name"]),
                    )
                    changed += 1
        return changed
