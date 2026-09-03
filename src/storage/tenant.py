"""Per-tenant records (Phase 2 M3-1): plans, seats, budgets, memberships.

Central single-file SQLite at the deployment root (`tenant.db`) — tenants are
deployment-level, not per-user. Minimal CRUD only; the money math lives in
the metering store (M1.1) and the billing router reads both.
"""

from __future__ import annotations

import sqlite3
import threading

from src.storage.paths import DataPaths

_PLANS = ("free", "seat", "smb")


class TenantError(ValueError):
    """Invalid tenant operation (unknown plan, etc.)."""


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'free',
            seat_count INTEGER NOT NULL DEFAULT 1,
            monthly_budget_usd REAL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memberships (
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            PRIMARY KEY (tenant_id, user_id)
        )
        """
    )
    # T3.1 migration (safe on existing M3 DBs): org/sub-tenant hierarchy.
    # 'firm' = pre-M3 deployment rows (unchanged semantics).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tenants)")}
    if "parent_tenant_id" not in cols:
        conn.execute("ALTER TABLE tenants ADD COLUMN parent_tenant_id TEXT")
    if "kind" not in cols:
        conn.execute(
            "ALTER TABLE tenants ADD COLUMN kind TEXT NOT NULL DEFAULT 'firm'"
        )
    conn.commit()
    return conn


class TenantStore:
    """Central tenant + membership records. Write via admin paths; the
    enforcement path reads only (tenant_for_user / members)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = _connect(db_path)

    def upsert_tenant(
        self,
        name: str,
        plan: str = "free",
        seat_count: int = 1,
        monthly_budget_usd: float | None = None,
    ) -> str:
        if plan not in _PLANS:
            raise TenantError(f"unknown plan {plan!r}; expected one of {_PLANS}")
        import uuid

        tid = uuid.uuid4().hex
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO tenants (id, name, plan, seat_count,
                                     monthly_budget_usd, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    name,
                    plan,
                    int(seat_count),
                    float(monthly_budget_usd) if monthly_budget_usd is not None else None,
                    datetime_now_iso(),
                ),
            )
        return tid

    def get_tenant(self, tenant_id: str) -> dict[str, object] | None:
        with self._lock, self._conn:
            self._conn.row_factory = sqlite3.Row
            row = self._conn.execute(
                "SELECT * FROM tenants WHERE id = ?", (tenant_id,)
            ).fetchone()
            self._conn.row_factory = None
        return dict(row) if row else None

    def set_plan(self, tenant_id: str, plan: str) -> None:
        if plan not in _PLANS:
            raise TenantError(f"unknown plan {plan!r}; expected one of {_PLANS}")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tenants SET plan = ? WHERE id = ?", (plan, tenant_id)
            )
        if cur.rowcount == 0:
            raise TenantError(f"no tenant {tenant_id!r}")

    def add_member(self, tenant_id: str, user_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO memberships (tenant_id, user_id) VALUES (?, ?)",
                (tenant_id, user_id),
            )

    def members(self, tenant_id: str) -> list[str]:
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT user_id FROM memberships WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
        return [r[0] for r in rows]

    def tenant_for_user(self, user_id: str) -> dict[str, object] | None:
        with self._lock, self._conn:
            self._conn.row_factory = sqlite3.Row
            row = self._conn.execute(
                """
                SELECT t.* FROM tenants t
                JOIN memberships m ON m.tenant_id = t.id
                WHERE m.user_id = ?
                """,
                (user_id,),
            ).fetchone()
            self._conn.row_factory = None
        return dict(row) if row else None


def datetime_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


_TENANT_STORE: TenantStore | None = None
_TENANT_LOCK = threading.Lock()


def get_tenant_store() -> TenantStore:
    """Central tenant store singleton at the deployment root (tenant.db)."""
    global _TENANT_STORE
    if _TENANT_STORE is not None:
        return _TENANT_STORE
    with _TENANT_LOCK:
        if _TENANT_STORE is not None:
            return _TENANT_STORE
        _TENANT_STORE = TenantStore(str(DataPaths(user_id=None).root / "tenant.db"))
        return _TENANT_STORE
