"""Fresh authority: no name-based namespace, role assertion or legacy-store adoption."""

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

RED = "1" * 32
BLUE = "2" * 32
STAFF = "a" * 32
CEO = "b" * 32
OPS = "c" * 32
RESEARCH = "d" * 32


def state_path(root: Path, instance_id: str = RED) -> Path:
    return root / "Instances" / instance_id / "private" / "engine-state.db"


async def provision(root: Path, instance_id: str = RED, mode: str = "MT"):
    from src.storage.instance_state import InstanceState

    state = InstanceState(state_path(root, instance_id))
    await state.initialize()
    await state.provision_instance(mode=mode, default_model="test:model-x")
    await state.provision_member(
        issuer="test-key-store", subject="staff", user_id=STAFF, role="staff"
    )
    return state


def identity(instance_id: str = RED, *, role: str = "staff"):
    from src.http.auth.resolver import UserIdentity

    return UserIdentity(
        user_id="staff",
        key_id="synthetic-key-id",
        trust_domain="untrusted",
        instance_id=instance_id,
        issuer="test-key-store",
        subject="staff",
        role=role,
    )


@pytest.mark.asyncio
async def test_equal_user_names_have_distinct_roots(tmp_path, monkeypatch):
    # Removing instance from the path would merge equal authenticated names.
    from src.storage.paths import instance_user_paths

    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path))
    red = await provision(tmp_path)
    blue = await provision(tmp_path, BLUE)
    red_actor = await red.authorize(identity(), RED)
    blue_actor = await blue.authorize(identity(BLUE), BLUE)
    red_paths = instance_user_paths(red_actor)
    blue_paths = instance_user_paths(blue_actor)
    assert red_paths.user_dir == tmp_path / "Instances" / RED / "Users" / STAFF
    assert blue_paths.user_dir == tmp_path / "Instances" / BLUE / "Users" / STAFF
    assert red_paths.root != blue_paths.root
    red_paths.files_dir().joinpath("sentinel.txt").write_text("red private")
    assert not blue_paths.files_dir().joinpath("sentinel.txt").exists()


@pytest.mark.asyncio
async def test_caller_cannot_assert_other_instance(tmp_path):
    state = await provision(tmp_path)
    with pytest.raises(PermissionError):
        await state.authorize(identity(BLUE), RED)
    with pytest.raises(PermissionError):
        await state.authorize(identity(), BLUE)


@pytest.mark.asyncio
async def test_identity_role_is_not_company_role(tmp_path):
    state = await provision(tmp_path)
    actor = await state.authorize(identity(role="admin"), RED)
    assert actor.role == "staff"
    assert actor.user_id == STAFF


@pytest.mark.asyncio
async def test_multiple_team_memberships(tmp_path):
    state = await provision(tmp_path)
    await state.provision_team(OPS)
    await state.provision_team(RESEARCH)
    await state.set_team_member(OPS, STAFF, enabled=True)
    await state.set_team_member(RESEARCH, STAFF, enabled=True)
    actor = await state.authorize(identity(), RED)
    assert set(await state.teams_of(actor)) == {OPS, RESEARCH}
    assert actor.instance_id == RED
    assert actor.role == "staff"
    await state.set_team_member(OPS, STAFF, enabled=False)
    with pytest.raises(PermissionError):
        await state.teams_of(actor)  # stale authority must not survive revocation
    current = await state.authorize(identity(), RED)
    assert await state.teams_of(current) == (RESEARCH,)


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["solo", "trusted-network", "desktop"])
async def test_shared_key_is_not_person_identity(tmp_path, domain):
    from src.http.auth.resolver import UserIdentity

    state = await provision(tmp_path)
    shared = UserIdentity(user_id="staff", key_id=None, trust_domain=domain)
    with pytest.raises(PermissionError):
        await state.authorize(shared, RED)


@pytest.mark.asyncio
async def test_old_layout_refused_without_mutation(tmp_path):
    from src.storage.instance_state import IncompatibleStoreError, InstanceState

    path = state_path(tmp_path)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE messages (content TEXT)")
        connection.execute("INSERT INTO messages VALUES ('retained customer content')")
    before = {p.name: p.read_bytes() for p in path.parent.iterdir()}
    with pytest.raises(IncompatibleStoreError):
        await InstanceState(path).initialize()
    assert {p.name: p.read_bytes() for p in path.parent.iterdir()} == before


@pytest.mark.asyncio
async def test_qualified_store_reopens_but_mismatched_instance_is_refused(tmp_path):
    from src.storage.instance_state import IncompatibleStoreError, InstanceState

    state = await provision(tmp_path)
    reopened = InstanceState(state_path(tmp_path))
    await reopened.initialize()
    assert await reopened.authorize(identity(), RED) == await state.authorize(identity(), RED)
    other = state_path(tmp_path, BLUE)
    other.parent.mkdir(parents=True)
    # Backups are stopped copies here; SQLite backup preserves actual committed data.
    with sqlite3.connect(state_path(tmp_path)) as source, sqlite3.connect(other) as target:
        source.backup(target)
    before = other.read_bytes()
    with pytest.raises(IncompatibleStoreError):
        await InstanceState(other).initialize()
    assert other.read_bytes() == before


@pytest.mark.asyncio
async def test_solo_provisions_explicit_identity_without_teams(tmp_path):
    state = await provision(tmp_path, mode="SR")
    actor = await state.authorize(identity(), RED)
    assert actor.user_id == STAFF
    assert await state.teams_of(actor) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", ["../escape", "default_user", "", "/absolute"])
async def test_malformed_namespace_refused_before_creation(tmp_path, bad_id):
    from src.storage.instance_state import InstanceState

    with pytest.raises(ValueError):
        await InstanceState(state_path(tmp_path, bad_id)).initialize()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_symlinked_private_store_refused(tmp_path):
    from src.storage.instance_state import InstanceState

    instance_root = state_path(tmp_path).parent.parent
    instance_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    instance_root.joinpath("private").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        await InstanceState(state_path(tmp_path)).initialize()
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_http_actor_uses_verified_seam_not_body_or_headers(tmp_path):
    from src.http.authority import resolve_actor

    state = await provision(tmp_path)
    request = Request({"type": "http", "headers": [(b"x-user-id", CEO.encode())]})
    request.state.identity = identity(role="admin")
    actor = await resolve_actor(request, state)
    assert actor.user_id == STAFF
    assert actor.role == "staff"
    request.state.identity = None
    with pytest.raises(HTTPException) as failure:
        await resolve_actor(request, state)
    assert failure.value.status_code == 401
