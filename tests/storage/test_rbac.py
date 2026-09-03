"""T3.2 store-level RBAC: roles, owner-demotion guard, helpers."""

from src.storage.tenancy import TenancyError, TenancyStore
from src.storage.tenant import TenantStore


def _store(tmp_path) -> TenancyStore:
    return TenancyStore(str(tmp_path / "tenant.db"))


def test_default_role_is_staff(tmp_path):
    s = _store(tmp_path)
    tid = s.create_org("acme")
    s.add_member(tid, "u1")
    assert s.role_of("u1") == "staff"


def test_owner_role_on_org_creation(tmp_path):
    s = _store(tmp_path)
    tid = s.create_org("acme", owner_id="founder")
    assert s.role_of("founder") == "owner"
    assert s.is_owner("founder") is True


def test_admin_at_least_helper(tmp_path):
    s = _store(tmp_path)
    tid = s.create_org("acme", owner_id="founder")
    s.add_member(tid, "mgr")
    s.set_role("mgr", "admin")
    assert s.at_least("mgr", "admin") is True
    assert s.at_least("mgr", "owner") is False
    assert s.is_admin("mgr") is True


def test_owner_cannot_drop_below_admin(tmp_path):
    s = _store(tmp_path)
    s.create_org("acme", owner_id="founder")
    try:
        s.set_role("founder", "staff")
        raise SystemExit(1)
    except TenancyError as e:
        assert "cannot be demoted below admin" in str(e)
    # admin is allowed for the original owner
    s.set_role("founder", "admin")
    assert s.role_of("founder") == "admin"


def test_non_owner_can_be_demoted(tmp_path):
    s = _store(tmp_path)
    tid = s.create_org("acme", owner_id="founder")
    s.add_member(tid, "mgr")
    s.set_role("mgr", "admin")
    s.set_role("mgr", "staff")
    assert s.role_of("mgr") == "staff"


def test_set_role_unknown_role_rejected(tmp_path):
    s = _store(tmp_path)
    tid = s.create_org("acme", owner_id="f")
    s.add_member(tid, "u1")
    try:
        s.set_role("u1", "superuser")
        raise AssertionError("expected rejection")
    except TenancyError as e:
        assert "unknown role" in str(e)


def test_set_role_requires_membership(tmp_path):
    s = _store(tmp_path)
    try:
        s.set_role("ghost", "admin")
        raise AssertionError("expected rejection")
    except TenancyError as e:
        assert "no membership" in str(e)


def test_m3_db_migration_preserves_rows(tmp_path):
    # A pre-T3.2 DB (memberships without role) still opens and keeps rows.
    legacy = TenantStore(str(tmp_path / "legacy.db"))
    tid = legacy.upsert_tenant(name="old", plan="seat")
    legacy.add_member(tid, "old_user")
    s = TenancyStore(str(tmp_path / "legacy.db"))
    assert s.role_of("old_user") == "staff"  # migration default
    s.set_role("old_user", "admin")
    assert s.role_of("old_user") == "admin"
