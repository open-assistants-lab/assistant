"""T3.1: org -> sub-tenant -> user tenancy on the M3 TenantStore.

Per the plan's key decision: per-user isolated stores REMAIN the single-writer
truth — these tables are mapping + aggregation only. No store-path redirection
happens here.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.storage.tenancy import TenancyError, TenancyStore


@pytest.fixture()
def store(tmp_path):
    return TenancyStore(str(tmp_path / "tenant.db"))


class TestOrgSubTenantCrud:
    def test_create_org_and_sub_tenant(self, store):
        org_id = store.create_org(name="Acme Legal")
        sub_id = store.create_sub_tenant(org_id, name="Acme Conveyancing")
        org = store.get_tenant(org_id)
        sub = store.get_tenant(sub_id)
        assert org["kind"] == "org"
        assert sub["kind"] == "sub_tenant"
        assert sub["parent_tenant_id"] == org_id

    def test_sub_tenant_requires_existing_org(self, tmp_path):
        store = TenancyStore(str(tmp_path / "tenant.db"))
        with pytest.raises(TenancyError, match="no tenant"):
            store.create_sub_tenant("missing", name="orphan")

    def test_move_membership(self, tmp_path):
        store = TenancyStore(str(tmp_path / "tenant.db"))
        org_id = store.create_org(name="O")
        sub_id = store.create_sub_tenant(org_id, name="S")
        store.add_member(sub_id, "alice")
        store.move_membership("alice", org_id)
        assert "alice" in store.members(org_id)
        assert "alice" not in store.members(sub_id)


class TestResolutionWalksToOrg:
    def test_tenant_for_user_walks_up_to_org(self, tmp_path):
        store = TenancyStore(str(tmp_path / "tenant.db"))
        org_id = store.create_org(name="Org")
        sub_id = store.create_sub_tenant(org_id, name="Sub")
        store.add_member(sub_id, "bob")
        resolved = store.resolve_membership("bob")
        assert resolved is not None
        assert resolved["id"] == sub_id
        assert resolved["org_id"] == org_id

    def test_unknown_user_resolves_none(self, tmp_path):
        store = TenancyStore(str(tmp_path / "tenant.db"))
        assert store.resolve_membership("nobody") is None


class TestMigrationSafety:
    def test_pre_existing_m3_db_gains_columns(self, tmp_path):

        db = tmp_path / "tenant.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE tenants (id TEXT PRIMARY KEY, name TEXT NOT NULL,"
            " plan TEXT NOT NULL DEFAULT 'free', seat_count INTEGER NOT NULL"
            " DEFAULT 1, monthly_budget_usd REAL, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE memberships (tenant_id TEXT NOT NULL,"
            " user_id TEXT NOT NULL, PRIMARY KEY (tenant_id, user_id))"
        )
        conn.execute(
            "INSERT INTO tenants (id, name, plan, seat_count, created_at)"
            " VALUES ('m3t', 'M3 Firm', 'seat', 2, '2026-09-01')"
        )
        conn.commit()
        conn.close()

        store = TenancyStore(str(db))
        row = store.get_tenant("m3t")
        assert row is not None
        assert row["kind"] == "firm"          # default for pre-M3 rows
        assert row["parent_tenant_id"] is None
        org_id = store.create_org(name="New Org")
        assert org_id


class TestMembershipUniqueness:
    def test_user_in_one_tenant_at_a_time(self, tmp_path):
        store = TenancyStore(str(tmp_path / "tenant.db"))
        t1 = store.create_org(name="A")
        t2 = store.create_org(name="B")
        store.add_member(t1, "carol")
        with pytest.raises(TenancyError, match="already a member"):
            store.add_member(t2, "carol")


class TestNoCrossTenantReadPath:
    def test_members_are_tenant_scoped(self, tmp_path):
        store = TenancyStore(str(tmp_path / "tenant.db"))
        o1 = store.create_org(name="O1")
        o2 = store.create_org(name="O2")
        store.add_member(o1, "dan")
        store.add_member(o2, "erin")
        assert store.members(o1) == ["dan"] or store.members(o1) == ["dan"]
        assert "erin" not in store.members(o1)
        assert "dan" not in store.members(o2)


class TestMovePreservesRole:
    """T3.2 review P1-1: move_membership must not demote admins/owners.

    The old implementation deleted + re-inserted without the role column,
    silently demoting an owner to staff (and escaping the set_role owner
    guard, since resolve_membership then returns the sub-tenant row).
    """

    def test_move_carries_role_forward(self, tmp_path):
        store = TenancyStore(str(tmp_path / "tenancy.db"))
        org_id = store.create_org(name="Org", owner_id="alice")
        sub_id = store.create_sub_tenant(org_id, name="Sub")
        store.set_role("alice", "owner")
        store.move_membership("alice", sub_id)
        assert store.role_of("alice") == "owner"  # carried, not demoted

    def test_move_admin_stays_admin(self, tmp_path):
        store = TenancyStore(str(tmp_path / "tenancy2.db"))
        org_id = store.create_org(name="Org", owner_id="alice")
        store.add_member(org_id, "bob")
        store.set_role("bob", "admin")
        sub_id = store.create_sub_tenant(org_id, name="Sub")
        store.move_membership("bob", sub_id)
        assert store.role_of("bob") == "admin"
        assert store.is_admin("bob")

    def test_move_staff_stays_staff(self, tmp_path):
        store = TenancyStore(str(tmp_path / "tenancy3.db"))
        org_id = store.create_org(name="Org", owner_id="alice")
        store.add_member(org_id, "carol")
        sub_id = store.create_sub_tenant(org_id, name="Sub")
        store.move_membership("carol", sub_id)
        assert store.role_of("carol") == "staff"
