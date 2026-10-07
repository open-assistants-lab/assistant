"""Actual generated credentials carry instance binding, not caller roles."""

import sqlite3
from dataclasses import replace

import pytest
from starlette.requests import Request

from tests.refactor.test_instance_authority import BLUE, RED, STAFF, provision


def test_actual_generated_key_resolves_instance_and_credential(tmp_path):
    from src.auth.keys import KeyStore

    store = KeyStore(str(tmp_path / "auth.db"))
    token = store.generate("staff", instance_id=RED)
    resolved = store.verify_identity(token)
    assert resolved is not None
    assert resolved.instance_id == RED
    assert resolved.issuer == "assistant-api-key"
    assert resolved.subject == "staff"
    assert resolved.key_id != token
    assert resolved.key_id != "oak"
    assert token not in (tmp_path / "auth.db").read_bytes().decode(errors="ignore")
    assert store.verify_identity("invalid token") is None
    store.revoke(token)
    assert store.verify_identity(token) is None


@pytest.mark.asyncio
async def test_actual_key_cannot_change_company_or_assert_admin(tmp_path):
    from src.auth.keys import KeyStore

    state = await provision(tmp_path)
    await state.provision_member(
        issuer="assistant-api-key", subject="staff", user_id=STAFF, role="staff"
    )
    store = KeyStore(str(tmp_path / "auth.db"))
    token = store.generate("staff", instance_id=RED)
    identity = store.verify_identity(token)
    assert (await state.authorize(replace(identity, role="admin"), RED)).role == "staff"
    with pytest.raises(PermissionError):
        await state.authorize(identity, BLUE)
    await state.provision_member(
        issuer="assistant-api-key", subject="staff", user_id=STAFF, role="staff", enabled=False
    )
    with pytest.raises(PermissionError):
        await state.authorize(identity, RED)


def test_unbound_legacy_key_not_individual_instance_identity(tmp_path):
    from src.auth.keys import KeyStore

    store = KeyStore(str(tmp_path / "auth.db"))
    token = store.generate("staff")
    assert store.verify_identity(token) is None


def test_old_key_database_refused_without_mutation(tmp_path):
    from src.auth.keys import KeyStore

    path = tmp_path / "old-auth.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE api_keys (key_hash TEXT PRIMARY KEY, user_id TEXT)")
        connection.execute("INSERT INTO api_keys VALUES ('old-hash', 'historical-owner')")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        KeyStore(str(path))
    assert path.read_bytes() == before


def test_key_store_cannot_adopt_other_retained_database(tmp_path):
    from src.auth.keys import KeyStore

    path = tmp_path / "retained.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE messages (content TEXT)")
        connection.execute("INSERT INTO messages VALUES ('retained private content')")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        KeyStore(str(path))
    assert path.read_bytes() == before


def test_per_user_resolver_preserves_verified_instance_binding(tmp_path, monkeypatch):
    from src.auth.keys import KeyStore
    from src.auth.resolver import PerUserKeyResolver

    store = KeyStore(str(tmp_path / "auth.db"))
    token = store.generate("staff", instance_id=RED)
    monkeypatch.setattr("src.auth.keys.get_key_store", lambda: store)
    request = Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})
    identity = PerUserKeyResolver().resolve(request)
    assert identity.instance_id == RED
    assert identity.issuer == "assistant-api-key"
    assert identity.subject == "staff"


def test_oidc_session_preserves_verified_issuer_subject_and_instance(monkeypatch):
    from types import SimpleNamespace

    from src.auth.oidc import SESSION_COOKIE, OidcResolver, OidcSessions
    from src.config.settings import get_settings

    sessions = OidcSessions()
    sid = sessions.create_session(
        "staff",
        "admin",
        instance_id=RED,
        issuer="https://synthetic-idp.test",
        subject="stable-subject",
    )
    monkeypatch.setattr("src.auth.oidc._store", lambda: sessions)
    monkeypatch.setattr(get_settings().oidc, "enabled", True)
    request = Request(
        {"type": "http", "headers": [(b"cookie", f"{SESSION_COOKIE}={sid}".encode())]}
    )
    identity = OidcResolver(SimpleNamespace(resolve=lambda _: None)).resolve(request)
    assert identity.instance_id == RED
    assert identity.issuer == "https://synthetic-idp.test"
    assert identity.subject == "stable-subject"
    assert identity.key_id != sid
    sessions.revoke_session(sid)
    assert OidcResolver(SimpleNamespace(resolve=lambda _: None)).resolve(request) is None
