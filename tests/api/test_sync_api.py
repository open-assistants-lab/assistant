"""Sync API contract tests — POST /workspaces/{id}/sync (P1-T2).

Read-only provider -> workspace Files/. Mock provider injected; no network.
"""

import pytest

from src.sdk.tools_core.file_sync import MockSyncAdapter, get_sync_registry


def _mock_files():
    return [
        ("r1", "plan.md", "rev1", b"# Plan"),
        ("r2", "budget.csv", "rev2", b"a,b\n1,2"),
        ("r3", "notes.txt", "rev3", b"notes"),
    ]


@pytest.fixture
def register_mock_provider():
    """Register a mock provider the sync router resolves by name."""
    registry = get_sync_registry()
    registry.register("mocksync", lambda user_id, workspace_id: MockSyncAdapter(_mock_files()))
    yield registry
    # Unregister so other tests are unaffected
    registry._factories.pop("mocksync", None)


def _sync(client, user_id, provider="mocksync"):
    return client.post(
        "/workspaces/personal/sync",
        params={"user_id": user_id, "provider": provider},
    )


def test_sync_endpoint_downloads_three_files(client, register_mock_provider, test_user_id):
    r = _sync(client, test_user_id)
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "mocksync"
    assert body["downloaded"] == 3
    assert body["failed"] == []
    assert set(body["files"]) == {"plan.md", "budget.csv", "notes.txt"}


def test_sync_endpoint_idempotent_by_remote_id(client, register_mock_provider, test_user_id):
    _sync(client, test_user_id)
    r = _sync(client, test_user_id)
    assert r.status_code == 200
    assert r.json()["downloaded"] == 0
    assert r.json()["skipped"] == 3


def test_sync_files_land_in_workspace_files_dir(client, register_mock_provider, test_user_id):
    from src.http.workspace_cache import get_file_cache
    from src.storage.paths import get_paths

    _sync(client, test_user_id)

    cache = get_file_cache(test_user_id)
    for name in ("plan.md", "budget.csv", "notes.txt"):
        assert cache.get_status(name) == "downloaded"

    # Files readable through the standard workspace read endpoint.
    r = client.get("/workspace/read/plan.md", params={"user_id": test_user_id})
    assert r.status_code == 200

    # On disk under the workspace files dir.
    root = get_paths(test_user_id, workspace_id="personal").workspace_files_dir()
    assert (root / "plan.md").read_text() == "# Plan"


def test_sync_unknown_provider_404(client, test_user_id):
    r = _sync(client, test_user_id, provider="nonexistent-provider")
    assert r.status_code == 404
    assert "nonexistent-provider" in r.json()["detail"]


def test_sync_revoked_connector_409_no_partial(client, test_user_id):
    """Registered provider with no stored credentials -> 409, nothing written."""
    from src.http.workspace_cache import get_file_cache

    r = _sync(client, test_user_id, provider="dropbox")
    assert r.status_code == 409
    assert "dropbox" in r.json()["detail"].lower()

    # Nothing partial landed.
    cache = get_file_cache(test_user_id)
    assert all(cache.get_status(n) == "cloud_only" for n in ("plan.md", "budget.csv", "notes.txt"))
