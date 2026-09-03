"""Org -> sub-tenant -> user tenancy (Phase 3 T3.1) on the M3 TenantStore.

KEY DECISION (plan T3.1): per-user isolated stores REMAIN the single-writer
truth. These tables are mapping + aggregation ONLY. "A user's store path
resolves under its tenant" is interpreted as IDENTITY/membership resolution
(walking memberships up to the org) — this module does NOT move or redirect
per-user data directories. Cross-tenant aggregation goes through the Phase 2
analytics sidecar (D1), never by scanning per-user stores.

Backed by the SAME tenant.db as M3's TenantStore (extended in place with
migration-safe ALTERs: tenants.kind 'org|sub_tenant|firm', tenants.
parent_tenant_id). Pre-M3 rows default kind='firm'.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid

from src.storage.paths import DataPaths
from src.storage.tenant import TenantError, TenantStore


class TenancyError(TenantError):
    """Invalid tenancy operation (orphan sub-tenant, duplicate membership)."""


class TenancyStore(TenantStore):
    """Org/sub-tenant layer over the M3 tenant tables.

    Same file, same connection discipline: write via admin paths, read-only
    enforcement helpers (resolve_membership / members).
    """

    def _require_tenant(self, tenant_id: str) -> dict[str, object]:
        row = self.get_tenant(tenant_id)
        if row is None:
            raise TenantError(f"no tenant {tenant_id!r}")
        return row

    # -- CRUD ----------------------------------------------------------------

    def create_org(self, name: str, owner_id: str | None = None) -> str:
        """Create a top-level org (kind='org', no parent).

        owner_id records the org creator — the RBAC original-owner guard
        (T3.2: an org's original owner cannot drop below admin) reads this.
        The creator's membership, when created, defaults to role 'owner'.
        """
        import uuid

        tid = uuid.uuid4().hex
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO tenants (id, name, plan, seat_count, kind,
                                     parent_tenant_id, owner_id, created_at)
                VALUES (?, ?, 'free', 1, 'org', NULL, ?, ?)
                """,
                (tid, name, owner_id, datetime_now_iso()),
            )
            if owner_id:
                self._conn.execute(
                    "INSERT OR IGNORE INTO memberships (tenant_id, user_id, role) "
                    "VALUES (?, ?, 'owner')",
                    (tid, owner_id),
                )
        return tid

    def create_sub_tenant(self, org_id: str, name: str) -> str:
        """Create a sub-tenant under an existing org."""
        try:
            parent = self._require_tenant(org_id)
        except TenantError as e:
            raise TenancyError(str(e)) from e
        if parent.get("kind") not in ("org", "sub_tenant"):
            # Firms (pre-M3 rows) can still become org parents by promotion,
            # but nesting under a firm is rejected to keep one root per org.
            raise TenancyError(
                f"tenant {org_id!r} is not an org; promote it first"
            )
        tid = uuid.uuid4().hex
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO tenants (id, name, plan, seat_count, kind,
                                     parent_tenant_id, created_at)
                VALUES (?, ?, 'free', 1, 'sub_tenant', ?, ?)
                """,
                (tid, name, org_id, datetime_now_iso()),
            )
        return tid

    def add_member(self, tenant_id: str, user_id: str) -> None:
        """Uniqueness invariant (T3.1): a user belongs to exactly one tenant
        at a time — a second tenancy raises instead of silently ignoring."""
        other = self.tenant_for_user(user_id)
        if other is not None and other["id"] != tenant_id:
            raise TenancyError(
                f"user {user_id!r} is already a member of tenant "
                f"{other['id']!r}; move_membership to reassign"
            )
        super().add_member(tenant_id, user_id)

    def move_membership(self, user_id: str, tenant_id: str) -> None:
        """Move a user's single membership to another tenant in the same org
        tree. Uniqueness invariant: a user belongs to exactly one tenant."""
        target = self._require_tenant(tenant_id)
        current = self.tenant_for_user(user_id)
        if current is not None and current["id"] == tenant_id:
            return
        if current is not None:
            cur_org = self.org_for_tenant(str(current["id"]))
            tgt_org = self.org_for_tenant(tenant_id)
            if cur_org != tgt_org:
                raise TenancyError(
                    "membership move is restricted to the same org tree"
                )
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM memberships WHERE user_id = ?", (user_id,)
            )
            self._conn.execute(
                "INSERT INTO memberships (tenant_id, user_id) VALUES (?, ?)",
                (tenant_id, user_id),
            )
        _ = target

    # -- RBAC (T3.2) ------------------------------------------------------

    _ROLE_RANK = {"staff": 0, "admin": 1, "owner": 2}

    def role_of(self, user_id: str) -> str:
        """The user's org-tree role; 'staff' when unaffiliated (default)."""
        m = self.resolve_membership(user_id)
        if m is None:
            return "staff"
        return str(m.get("role") or "staff")

    def is_owner(self, user_id: str) -> bool:
        return self.role_of(user_id) == "owner"

    def is_admin(self, user_id: str) -> bool:
        return self._ROLE_RANK.get(self.role_of(user_id), 0) >= 1

    def at_least(self, user_id: str, role: str) -> bool:
        return self._ROLE_RANK.get(self.role_of(user_id), 0) >= self._ROLE_RANK.get(role, 0)

    def set_role(self, user_id: str, role: str) -> None:
        """Set a membership role. Owner-demotion guard (T3.2): an org's
        ORIGINAL owner (tenants.owner_id) cannot drop below admin. Role
        changes require admin+ at the ROUTER layer; the store enforces the
        demotion invariant itself so no code path can bypass it."""
        if role not in self._ROLE_RANK:
            raise TenancyError(f"unknown role {role!r}")
        m = self.resolve_membership(user_id)
        if m is None:
            raise TenancyError(f"user {user_id!r} has no membership")
        tenant = self._require_tenant(str(m["tenant_id"]))
        if tenant.get("owner_id") == user_id:
            new_rank = self._ROLE_RANK.get(role, 0)
            if new_rank < 1:
                raise TenancyError(
                    f"user {user_id!r} is the original owner of tenant "
                    f"{tenant['id']!r} and cannot be demoted below admin"
                )
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE memberships SET role = ? WHERE user_id = ?",
                (role, user_id),
            )
            if cur.rowcount == 0:
                raise TenancyError(f"user {user_id!r} has no membership")

    # -- resolution ------------------------------------------------------

    def org_for_tenant(self, tenant_id: str) -> str:
        """Walk parent_tenant_id up to the org root; returns the org id (or
        the tenant's own id when it IS the org / a firm)."""
        seen: set[str] = set()
        cur = self._require_tenant(tenant_id)
        while cur.get("parent_tenant_id"):
            pid = str(cur["parent_tenant_id"])
            if pid in seen:
                raise TenancyError("tenancy cycle detected")
            seen.add(pid)
            cur = self._require_tenant(pid)
        return str(cur["id"])

    def resolve_membership(self, user_id: str) -> dict[str, object] | None:
        """The user's tenant row PLUS the org id it rolls up to. None when the
        user has no membership. This is the enforcement-side read: mapping
        only, never a store path."""
        with self._lock, self._conn:
            self._conn.row_factory = sqlite3.Row
            row = self._conn.execute(
                """
                SELECT t.*, m.role AS membership_role
                FROM tenants t
                JOIN memberships m ON m.tenant_id = t.id
                WHERE m.user_id = ?
                """,
                (user_id,),
            ).fetchone()
            self._conn.row_factory = None
        if row is None:
            return None
        out = dict(row)
        out["role"] = out.get("membership_role") or "staff"
        out["tenant_id"] = out["id"]
        out["org_id"] = self.org_for_tenant(str(out["id"]))
        return out

    def member_rows(self, tenant_id: str) -> list[dict[str, object]]:
        """Members of this tenant (and its sub-tenants when the tenant is an
        org) — the tenant-admin listing surface. Tenant-scoped: only rows in
        this org tree appear."""
        ids = [tenant_id]
        with self._lock, self._conn:
            subs = self._conn.execute(
                "SELECT id FROM tenants WHERE parent_tenant_id = ?",
                (tenant_id,),
            ).fetchall()
        ids.extend(r[0] for r in subs)
        out: list[dict[str, object]] = []
        for tid in ids:
            for uid in self.members(tid):
                out.append({"user_id": uid, "tenant_id": tid})
        return out


def datetime_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


_TENANCY_STORE: TenancyStore | None = None
_TENANCY_LOCK = threading.Lock()


def get_tenancy_store() -> TenancyStore:
    """Same tenant.db file as get_tenant_store — shared singleton so the M3
    billing router and T3.1 tenancy routes see identical rows."""
    global _TENANCY_STORE
    if _TENANCY_STORE is not None:
        return _TENANCY_STORE
    with _TENANCY_LOCK:
        if _TENANCY_STORE is not None:
            return _TENANCY_STORE
        _TENANCY_STORE = TenancyStore(
            str(DataPaths(user_id=None).root / "tenant.db")
        )
        return _TENANCY_STORE
