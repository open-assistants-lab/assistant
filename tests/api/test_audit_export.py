"""Audit export endpoint tests (roadmap P0-T4).

Proves: audit rows written by the CaptureBus round-trip through GET /audit as
NDJSON; per-user isolation; since filter; and P0-T2 enforcement (a resolved
per-user identity must match the requested user_id).
"""

import json

import pytest

from src.sdk.audit import AuditEvent


def _write_events(user_id: str, n: int = 2, tool: str = "time_get") -> None:
    """Write events through the real store the loop uses."""
    from src.sdk.audit import ensure_audit_store_subscribed

    store = ensure_audit_store_subscribed(user_id)
    for i in range(n):
        store.record(
            AuditEvent(
                event_id=f"{user_id}-e{i}",
                ts=__import__("datetime").datetime(2026, 8, 25, 10, 0, i),
                user_id=user_id,
                session_id=f"sess-{i}",
                kind="tool_call",
                tool=tool,
                call_id=f"call_{i}",
                approved=None,
                detail="event 0" if i == 0 else "event 1",
            )
        )


def test_export_returns_ndjson_rows(client):
    _write_events("audit_u1", n=2)
    r = client.get("/audit", params={"user_id": "audit_u1"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(x) for x in r.text.strip().splitlines()]
    assert len(lines) == 2
    assert all(row["user_id"] == "audit_u1" for row in lines)
    assert [row["tool"] for row in lines] == ["time_get", "time_get"]
    assert r.headers.get("x-audit-count") == "2"


def test_export_isolates_users(client):
    _write_events("audit_u1", n=2)
    _write_events("audit_u2", n=1, tool="email_search")
    r = client.get("/audit", params={"user_id": "audit_u1"})
    lines = [json.loads(x) for x in r.text.strip().splitlines()]
    assert len(lines) == 2
    assert all(row["user_id"] == "audit_u1" for row in lines)


def test_export_since_filter(client):
    from datetime import datetime

    _write_events("audit_u3", n=2)
    r = client.get(
        "/audit",
        params={"user_id": "audit_u3", "since": "2026-08-25T10:00:01"},
    )
    lines = [json.loads(x) for x in r.text.strip().splitlines()]
    assert len(lines) == 1
    assert lines[0]["event_id"] == "audit_u3-e1"


def test_export_invalid_since_400(client):
    r = client.get("/audit", params={"user_id": "audit_u1", "since": "not-a-date"})
    assert r.status_code == 400


def test_export_empty_user_returns_empty(client):
    r = client.get("/audit", params={"user_id": "no_such_user"})
    assert r.status_code == 200
    assert r.text.strip() == ""
    assert r.headers.get("x-audit-count") == "0"


def test_export_mismatched_user_id_403_when_per_user_resolver(client, monkeypatch):
    """P0-T2: with a per-user resolver active, requesting another user's audit
    must 403. Reuses the injected-resolver pattern from test_identity_resolver."""
    from src.config import reload_settings

    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("SOLO_BYPASS", "false")
    reload_settings()

    class _PerUserResolver:
        def resolve(self, request):
            from src.http.auth.resolver import UserIdentity

            return UserIdentity(
                user_id="charlie", key_id="k1", trust_domain="trusted-network"
            )

    monkeypatch.setattr("src.http.auth.get_resolver", lambda: _PerUserResolver())
    r = client.get(
        "/audit",
        params={"user_id": "alice"},
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 403

    # Matching identity is allowed through to the export (may be empty).
    r = client.get(
        "/audit",
        params={"user_id": "charlie"},
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 200

    from src.config import reload_settings as _rs

    _rs()


@pytest.fixture(autouse=True)
def _restore_settings_after_each():
    """Keep the process-global settings singleton clean (pollution fix,
    same pattern as test_identity_resolver)."""
    yield
    from src.config import reload_settings

    reload_settings()
