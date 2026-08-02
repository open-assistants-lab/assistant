from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.routing import APIRoute

from src.config.user_settings_store import SettingsWriteError, UserSettingsStore
from src.http.routers import user_prompt as user_prompt_router
from src.storage.paths import DataPaths

SEED_PATH = Path(__file__).resolve().parents[2] / "seeds" / "prompts" / "grader_prompt.md"
SEED = SEED_PATH.read_text(encoding="utf-8")


@pytest.fixture
def grader_prompt_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    stores: dict[str, UserSettingsStore] = {}
    resets: list[tuple[str, str]] = []

    def get_store(user_id: str) -> UserSettingsStore:
        if user_id not in stores:
            paths = DataPaths(
                user_id=user_id,
                data_path=str(tmp_path / "data"),
                ea_root=str(tmp_path / "root"),
            )
            stores[user_id] = UserSettingsStore(
                user_id,
                paths=paths,
                legacy_path=tmp_path / "legacy" / user_id / "settings.json",
            )
        return stores[user_id]

    monkeypatch.setattr(user_prompt_router, "_get_grader_prompt_store", get_store)
    monkeypatch.setattr(
        user_prompt_router,
        "_reset_grader_prompt_loops",
        lambda user_id: resets.append((user_id, "grader_prompt_changed")),
    )
    return {"get_store": get_store, "resets": resets, "root": tmp_path / "root"}


def _expected(content: str, source: str, revision: int) -> dict[str, object]:
    return {
        "content": content,
        "source": source,
        "content_hash": f"sha256:{hashlib.sha256(content.encode()).hexdigest()}",
        "revision": revision,
    }


def test_get_first_access_seeds_exact_default_and_remains_stable(
    client, grader_prompt_api, test_user_id
):
    first = client.get("/user/grader-prompt", params={"user_id": test_user_id})
    second = client.get("/user/grader-prompt", params={"user_id": test_user_id})

    assert first.status_code == 200
    assert first.json() == _expected(SEED, "seeded", 0)
    assert second.json() == first.json()
    store = grader_prompt_api["get_store"](test_user_id)
    assert store.load_grader_prompt().content == SEED


def test_put_preserves_exact_content_and_resets_only_target_user(
    client, grader_prompt_api, test_user_id
):
    content = "  Exact custom rubric\n\nKeep final newline.\n"

    response = client.put(
        "/user/grader-prompt",
        params={"user_id": test_user_id},
        json={"content": content, "expected_revision": 0},
    )

    assert response.status_code == 200
    assert response.json() == _expected(content, "customized", 1)
    assert grader_prompt_api["get_store"](test_user_id).load_grader_prompt().content == content
    assert grader_prompt_api["resets"] == [(test_user_id, "grader_prompt_changed")]


def test_same_content_put_is_noop(client, grader_prompt_api, test_user_id):
    client.get("/user/grader-prompt", params={"user_id": test_user_id})

    response = client.put(
        "/user/grader-prompt",
        params={"user_id": test_user_id},
        json={"content": SEED, "expected_revision": 0},
    )

    assert response.status_code == 200
    assert response.json() == _expected(SEED, "seeded", 0)
    assert grader_prompt_api["resets"] == []


def test_blank_put_is_secret_free_and_unchanged(client, grader_prompt_api, test_user_id):
    before = client.get("/user/grader-prompt", params={"user_id": test_user_id}).json()

    response = client.put(
        "/user/grader-prompt",
        params={"user_id": test_user_id},
        json={"content": " \n\t", "expected_revision": 0},
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "validation_error",
        "message": "Invalid settings request",
        "details": {},
    }
    assert client.get("/user/grader-prompt", params={"user_id": test_user_id}).json() == before
    assert grader_prompt_api["resets"] == []


@pytest.mark.parametrize("operation", ["put", "reset"])
def test_stale_mutations_return_exact_conflict_without_changes(
    client, grader_prompt_api, test_user_id, operation
):
    custom = "current rubric\n"
    client.put(
        "/user/grader-prompt",
        params={"user_id": test_user_id},
        json={"content": custom, "expected_revision": 0},
    )
    grader_prompt_api["resets"].clear()

    if operation == "put":
        response = client.put(
            "/user/grader-prompt",
            params={"user_id": test_user_id},
            json={"content": "stale secret", "expected_revision": 0},
        )
    else:
        response = client.post(
            "/user/grader-prompt/reset",
            params={"user_id": test_user_id},
            json={"expected_revision": 0},
        )

    assert response.status_code == 409
    assert response.json() == {
        "code": "revision_conflict",
        "message": "Settings revision conflict",
        "details": {"expected": 0, "actual": 1},
    }
    current = client.get("/user/grader-prompt", params={"user_id": test_user_id}).json()
    assert current == _expected(custom, "customized", 1)
    assert grader_prompt_api["resets"] == []


def test_reset_restores_packaged_seed_then_default_reset_is_noop(
    client, grader_prompt_api, test_user_id
):
    client.put(
        "/user/grader-prompt",
        params={"user_id": test_user_id},
        json={"content": "custom", "expected_revision": 0},
    )
    grader_prompt_api["resets"].clear()

    changed = client.post(
        "/user/grader-prompt/reset",
        params={"user_id": test_user_id},
        json={"expected_revision": 1},
    )
    noop = client.post(
        "/user/grader-prompt/reset",
        params={"user_id": test_user_id},
        json={"expected_revision": 2},
    )

    assert changed.status_code == 200
    assert changed.json() == _expected(SEED, "seeded", 2)
    assert noop.json() == _expected(SEED, "seeded", 2)
    assert grader_prompt_api["resets"] == [(test_user_id, "grader_prompt_changed")]


def test_prompts_are_isolated_by_user(
    client, grader_prompt_api, test_user_id, test_user_id_2
):
    client.put(
        "/user/grader-prompt",
        params={"user_id": test_user_id},
        json={"content": "alice only", "expected_revision": 0},
    )

    alice = client.get("/user/grader-prompt", params={"user_id": test_user_id}).json()
    bob = client.get("/user/grader-prompt", params={"user_id": test_user_id_2}).json()

    assert alice == _expected("alice only", "customized", 1)
    assert bob == _expected(SEED, "seeded", 0)


def test_missing_seed_uses_injected_host_default(client, monkeypatch, tmp_path, test_user_id):
    fallback = "  host rubric exact\n"
    paths = DataPaths(
        user_id=test_user_id,
        data_path=str(tmp_path / "data"),
        ea_root=str(tmp_path / "root"),
    )
    store = UserSettingsStore(
        test_user_id,
        paths=paths,
        grader_seed_path=tmp_path / "missing.md",
        legacy_default_rubric=fallback,
    )
    monkeypatch.setattr(user_prompt_router, "_get_grader_prompt_store", lambda user_id: store)

    response = client.get("/user/grader-prompt", params={"user_id": test_user_id})

    assert response.status_code == 200
    assert response.json() == _expected(fallback, "seeded", 0)


def test_missing_seed_and_host_default_is_controlled(client, monkeypatch, tmp_path, test_user_id):
    paths = DataPaths(
        user_id=test_user_id,
        data_path=str(tmp_path / "data"),
        ea_root=str(tmp_path / "root"),
    )
    store = UserSettingsStore(
        test_user_id,
        paths=paths,
        grader_seed_path=tmp_path / "missing.md",
        legacy_default_rubric="",
    )
    monkeypatch.setattr(user_prompt_router, "_get_grader_prompt_store", lambda user_id: store)

    response = client.get("/user/grader-prompt", params={"user_id": test_user_id})

    assert response.status_code == 500
    assert response.json() == {
        "code": "configuration_error",
        "message": "Unable to process user settings",
        "details": {},
    }


@pytest.mark.parametrize("failure_point", ["prompt", "settings"])
def test_write_failures_are_controlled_without_reset_or_durable_drift(
    client, grader_prompt_api, monkeypatch, test_user_id, failure_point
):
    secret = "prompt-secret-must-not-leak"
    store = grader_prompt_api["get_store"](test_user_id)
    before = client.get("/user/grader-prompt", params={"user_id": test_user_id}).json()
    if failure_point == "prompt":
        monkeypatch.setattr(
            store,
            "_atomic_write_prompt",
            lambda content: (_ for _ in ()).throw(SettingsWriteError("secret provider key")),
        )
    else:
        monkeypatch.setattr(
            store,
            "_atomic_write",
            lambda settings: (_ for _ in ()).throw(SettingsWriteError("secret provider key")),
        )

    response = client.put(
        "/user/grader-prompt",
        params={"user_id": test_user_id},
        json={"content": secret, "expected_revision": 0},
    )

    assert response.status_code == 500
    assert response.json() == {
        "code": "configuration_error",
        "message": "Unable to process user settings",
        "details": {},
    }
    assert secret not in response.text
    assert "provider key" not in response.text
    assert grader_prompt_api["resets"] == []
    assert store.load_grader_prompt().model_dump(mode="json") == before


def test_traversal_user_is_controlled_without_escape(client, grader_prompt_api, tmp_path):
    response = client.get("/user/grader-prompt", params={"user_id": "../escape-secret"})

    assert response.status_code == 422
    assert response.json() == {
        "code": "validation_error",
        "message": "Invalid settings request",
        "details": {},
    }
    assert "escape-secret" not in response.text
    assert not (tmp_path / "escape-secret").exists()
    assert grader_prompt_api["resets"] == []


def test_grader_prompt_store_factory_injects_host_default(monkeypatch):
    captured: dict[str, object] = {}

    class FakeStore:
        def __init__(self, user_id: str, *, legacy_default_rubric: str) -> None:
            captured.update(user_id=user_id, rubric=legacy_default_rubric)

    monkeypatch.setattr(user_prompt_router, "UserSettingsStore", FakeStore)
    monkeypatch.setattr(
        user_prompt_router,
        "get_settings",
        lambda: SimpleNamespace(verification=SimpleNamespace(default_rubric="host default")),
    )

    result = user_prompt_router._get_grader_prompt_store("alice")

    assert isinstance(result, FakeStore)
    assert captured == {"user_id": "alice", "rubric": "host default"}


def test_grader_prompt_route_endpoints_are_synchronous(app):
    endpoints = {
        route.path: route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/user/grader-prompt")
    }

    assert set(endpoints) == {"/user/grader-prompt", "/user/grader-prompt/reset"}
    assert all(not inspect.iscoroutinefunction(endpoint) for endpoint in endpoints.values())
