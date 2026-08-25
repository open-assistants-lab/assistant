from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.routing import APIRoute

from src.config.user_settings import GraderPromptUpdate
from src.config.user_settings_store import (
    RevisionConflict,
    SettingsWriteError,
    UserSettingsStore,
)
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
                data_root=str(tmp_path / "root"),
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


VALIDATION_ERROR = {
    "code": "validation_error",
    "message": "Invalid settings request",
    "details": {},
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
    assert response.json() == VALIDATION_ERROR
    assert client.get("/user/grader-prompt", params={"user_id": test_user_id}).json() == before
    assert grader_prompt_api["resets"] == []


@pytest.mark.parametrize(
    ("method", "path"),
    [("put", "/user/grader-prompt"), ("post", "/user/grader-prompt/reset")],
)
def test_missing_body_returns_canonical_validation_error(
    client, grader_prompt_api, test_user_id, method, path
):
    response = client.request(method, path, params={"user_id": test_user_id}, content=b"")

    assert response.status_code == 422
    assert response.json() == VALIDATION_ERROR
    assert grader_prompt_api["resets"] == []


@pytest.mark.parametrize(
    ("method", "path"),
    [("put", "/user/grader-prompt"), ("post", "/user/grader-prompt/reset")],
)
def test_malformed_json_returns_redacted_validation_error(
    client, grader_prompt_api, test_user_id, method, path
):
    secret = "malformed-sensitive-prompt"
    response = client.request(
        method,
        path,
        params={"user_id": test_user_id},
        content=f'{{"content":"{secret}"'.encode(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json() == VALIDATION_ERROR
    assert secret not in response.text
    assert grader_prompt_api["resets"] == []


@pytest.mark.parametrize(
    ("method", "path"),
    [("put", "/user/grader-prompt"), ("post", "/user/grader-prompt/reset")],
)
def test_wrong_json_type_returns_redacted_validation_error(
    client, grader_prompt_api, test_user_id, method, path
):
    secret = "wrong-type-sensitive-prompt"
    response = client.request(
        method,
        path,
        params={"user_id": test_user_id},
        json=[secret],
    )

    assert response.status_code == 422
    assert response.json() == VALIDATION_ERROR
    assert secret not in response.text
    assert grader_prompt_api["resets"] == []


def test_invalid_put_never_echoes_sensitive_submitted_content(
    client, grader_prompt_api, test_user_id
):
    secret = "valid-json-sensitive-prompt"

    response = client.put(
        "/user/grader-prompt",
        params={"user_id": test_user_id},
        json={"content": secret, "expected_revision": -1},
    )

    assert response.status_code == 422
    assert response.json() == VALIDATION_ERROR
    assert secret not in response.text
    assert grader_prompt_api["resets"] == []


@pytest.mark.parametrize("operation", ["put", "reset"])
def test_stale_mutations_return_exact_conflict_without_changes(
    client, grader_prompt_api, monkeypatch, test_user_id, operation
):
    custom = "current rubric\n"
    client.put(
        "/user/grader-prompt",
        params={"user_id": test_user_id},
        json={"content": custom, "expected_revision": 0},
    )
    grader_prompt_api["resets"].clear()
    threadpool_calls: list[str] = []

    async def run_in_threadpool(function, *args):
        threadpool_calls.append(function.__name__)
        return function(*args)

    monkeypatch.setattr(user_prompt_router, "run_in_threadpool", run_in_threadpool)

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
        "details": {
            "expected": 0,
            "actual": 1,
            "latest": _expected(custom, "customized", 1),
        },
    }
    assert "stale secret" not in response.text
    current = client.get("/user/grader-prompt", params={"user_id": test_user_id}).json()
    assert current == _expected(custom, "customized", 1)
    mutation_helper = (
        "_save_grader_prompt_sync" if operation == "put" else "_reset_grader_prompt_sync"
    )
    assert threadpool_calls == [mutation_helper, "_grader_prompt_conflict_details_sync"]
    assert grader_prompt_api["resets"] == []


def test_conflict_latest_failure_is_redacted_and_does_not_reset(
    client, grader_prompt_api, monkeypatch, test_user_id
):
    store = grader_prompt_api["get_store"](test_user_id)
    store.save_grader_prompt(GraderPromptUpdate(content="current", expected_revision=0))
    grader_prompt_api["resets"].clear()
    original_save = store.save_grader_prompt

    def conflict_then_break_load(update):
        try:
            return original_save(update)
        except RevisionConflict:
            monkeypatch.setattr(
                store,
                "load_grader_prompt",
                lambda: (_ for _ in ()).throw(RuntimeError("latest secret")),
            )
            raise

    monkeypatch.setattr(store, "save_grader_prompt", conflict_then_break_load)

    response = client.put(
        "/user/grader-prompt",
        params={"user_id": test_user_id},
        json={"content": "stale secret", "expected_revision": 0},
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "revision_conflict",
        "message": "Settings revision conflict",
        "details": {
            "expected": 0,
            "actual": 1,
            "latest_error": "configuration_error",
        },
    }
    assert "secret" not in response.text
    assert grader_prompt_api["resets"] == []


def test_get_blank_persisted_prompt_returns_controlled_configuration_error(
    client, grader_prompt_api, test_user_id
):
    store = grader_prompt_api["get_store"](test_user_id)
    store._grader_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    store._grader_prompt_path.write_text(" \n\t", encoding="utf-8")

    response = client.get("/user/grader-prompt", params={"user_id": test_user_id})

    assert response.status_code == 500
    assert response.json() == {
        "code": "configuration_error",
        "message": "Unable to process user settings",
        "details": {},
    }
    assert store._grader_prompt_path.read_text(encoding="utf-8") == " \n\t"


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
        data_root=str(tmp_path / "root"),
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
        data_root=str(tmp_path / "root"),
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
    assert response.json() == VALIDATION_ERROR
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


@pytest.mark.parametrize(
    ("operation", "helper", "method", "path"),
    [
        (
            "save_grader_prompt",
            "_save_grader_prompt_sync",
            "put",
            "/user/grader-prompt",
        ),
        (
            "reset_grader_prompt",
            "_reset_grader_prompt_sync",
            "post",
            "/user/grader-prompt/reset",
        ),
    ],
)
def test_grader_prompt_store_creation_and_mutation_use_threadpool(
    client, grader_prompt_api, monkeypatch, test_user_id, operation, helper, method, path
):
    store = grader_prompt_api["get_store"](test_user_id)
    if operation == "reset_grader_prompt":
        store.save_grader_prompt(
            user_prompt_router.GraderPromptUpdate(content="custom", expected_revision=0)
        )
        body = {"expected_revision": 1}
    else:
        body = {"content": "custom", "expected_revision": 0}
    original_mutation = getattr(store, operation)
    inside_threadpool = False
    threadpool_calls: list[str] = []
    factory_calls: list[str] = []
    mutation_calls: list[str] = []

    def get_store(user_id):
        assert inside_threadpool
        factory_calls.append(user_id)
        return store

    def mutate(request):
        assert inside_threadpool
        mutation_calls.append(operation)
        return original_mutation(request)

    async def run_in_threadpool(function, *args):
        nonlocal inside_threadpool
        threadpool_calls.append(function.__name__)
        inside_threadpool = True
        try:
            return function(*args)
        finally:
            inside_threadpool = False

    monkeypatch.setattr(user_prompt_router, "_get_grader_prompt_store", get_store)
    monkeypatch.setattr(store, operation, mutate)
    monkeypatch.setattr(user_prompt_router, "run_in_threadpool", run_in_threadpool)

    response = client.request(method, path, params={"user_id": test_user_id}, json=body)

    assert response.status_code == 200
    assert threadpool_calls == [helper]
    assert factory_calls == [test_user_id]
    assert mutation_calls == [operation]


def test_grader_prompt_route_execution_models(app):
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/user/grader-prompt")
    ]
    get_route = next(route for route in routes if route.path == "/user/grader-prompt" and "GET" in route.methods)
    put_route = next(route for route in routes if route.path == "/user/grader-prompt" and "PUT" in route.methods)
    reset_route = next(route for route in routes if route.path.endswith("/reset"))

    assert not inspect.iscoroutinefunction(get_route.endpoint)
    assert inspect.iscoroutinefunction(put_route.endpoint)
    assert inspect.iscoroutinefunction(reset_route.endpoint)
