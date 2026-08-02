from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from src.config.user_settings import UserSettingsPatch, VerificationOverrides
from src.config.user_settings_store import (
    SettingsConfigurationError,
    SettingsWriteError,
    UserSettingsStore,
)
from src.http.routers import settings as settings_router
from src.storage.paths import DataPaths


@pytest.fixture
def settings_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    stores: dict[str, UserSettingsStore] = {}

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

    providers = [
        {"id": "ollama", "name": "Ollama", "env": []},
        {"id": "openai", "name": "OpenAI", "env": ["OPENAI_API_KEY"]},
    ]
    models = {
        "ollama": [
            {
                "id": "ollama:minimax-m2.5",
                "name": "MiniMax M2.5",
                "provider": "ollama",
                "provider_display": "Ollama",
            }
        ],
        "openai": [
            {
                "id": "openai:gpt-4.1",
                "name": "GPT-4.1",
                "provider": "openai",
                "provider_display": "OpenAI",
            }
        ],
    }
    resets: list[tuple[str, str | None]] = []
    monkeypatch.setattr(settings_router, "_get_settings_store", get_store)
    monkeypatch.setattr(settings_router, "_catalog_providers", lambda: providers)
    monkeypatch.setattr(
        settings_router,
        "_provider_models",
        lambda provider_id, provider_name: models.get(provider_id, []),
    )
    monkeypatch.setattr(
        settings_router,
        "_reset_user_loops",
        lambda user_id: resets.append((user_id, "settings_changed")),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    return {"stores": stores, "get_store": get_store, "resets": resets}


def test_get_settings_returns_canonical_secret_free_shape(client, settings_api, test_user_id):
    secret = "settings-super-secret"
    settings_api["get_store"](test_user_id).set_provider_key("openai", secret)

    response = client.get("/settings", params={"user_id": test_user_id})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"schema_version", "revision", "saved", "effective", "provider_status"}
    assert set(body["saved"]) == {"default_model", "verification"}
    assert body["provider_status"]["openai"] == {
        "name": "OpenAI",
        "has_key": True,
        "key_configured_via_env": False,
        "key_source": "user",
    }
    assert "provider_keys" not in response.text
    assert secret not in response.text


def test_get_settings_distinguishes_saved_and_effective_values(client, settings_api, test_user_id):
    store = settings_api["get_store"](test_user_id)
    store.set_provider_key("openai", "key")
    current = store.load()
    store.patch(
        UserSettingsPatch(
            expected_revision=current.revision,
            default_model="openai:gpt-4.1",
            verification=VerificationOverrides(
                enabled=True, grader_model="openai:gpt-4.1", max_attempts=2
            ),
        )
    )

    body = client.get("/settings", params={"user_id": test_user_id}).json()

    assert body["saved"] == {
        "default_model": "openai:gpt-4.1",
        "verification": {
            "enabled": True,
            "grader_model": "openai:gpt-4.1",
            "max_attempts": 2,
        },
    }
    assert body["effective"]["default_model"] == "openai:gpt-4.1"
    assert body["effective"]["verification"]["state"] == "on"
    assert body["effective"]["verification"]["grader_prompt_hash"].startswith("sha256:")


def test_get_settings_treats_missing_default_prompt_as_unavailable(
    client, settings_api, monkeypatch, test_user_id
):
    store = settings_api["get_store"](test_user_id)
    store.patch(
        UserSettingsPatch(
            expected_revision=0,
            verification=VerificationOverrides(enabled=True),
        )
    )
    monkeypatch.setattr(
        store,
        "load_grader_prompt",
        lambda: (_ for _ in ()).throw(
            SettingsConfigurationError("No default grader prompt is configured")
        ),
    )

    response = client.get("/settings", params={"user_id": test_user_id})

    assert response.status_code == 200
    assert response.json()["effective"]["verification"]["unavailable_reason"] == "missing_prompt"


def test_patch_revision_conflict_and_legacy_revision_compatibility(
    client, settings_api, test_user_id
):
    first = client.patch(
        "/settings",
        params={"user_id": test_user_id},
        json={"expected_revision": 0, "default_model": "openai:gpt-4.1"},
    )
    stale = client.patch(
        "/settings",
        params={"user_id": test_user_id},
        json={"expected_revision": 0, "default_model": "ollama:minimax-m2.5"},
    )
    legacy = client.patch(
        "/settings",
        params={"user_id": test_user_id},
        json={"default_model": "ollama:minimax-m2.5"},
    )

    assert first.status_code == 200
    assert first.json()["revision"] == 1
    assert stale.status_code == 409
    assert stale.json() == {
        "code": "revision_conflict",
        "message": "Settings revision conflict",
        "details": {"expected": 0, "actual": 1},
    }
    assert legacy.status_code == 200
    assert legacy.json()["revision"] == 2
    assert settings_api["resets"] == [
        (test_user_id, "settings_changed"),
        (test_user_id, "settings_changed"),
    ]


def test_patch_explicit_null_clears_while_omitted_fields_are_preserved(
    client, settings_api, test_user_id
):
    set_values = client.patch(
        "/settings",
        params={"user_id": test_user_id},
        json={
            "expected_revision": 0,
            "default_model": "openai:gpt-4.1",
            "verification": {"enabled": True, "max_attempts": 2},
        },
    ).json()
    verification_only = client.patch(
        "/settings",
        params={"user_id": test_user_id},
        json={"expected_revision": set_values["revision"], "verification": {"enabled": False}},
    ).json()
    cleared = client.patch(
        "/settings",
        params={"user_id": test_user_id},
        json={
            "expected_revision": verification_only["revision"],
            "default_model": None,
            "verification": {"enabled": None},
        },
    ).json()

    assert verification_only["saved"]["default_model"] == "openai:gpt-4.1"
    assert verification_only["saved"]["verification"] == {
        "enabled": False,
        "grader_model": None,
        "max_attempts": 2,
    }
    assert verification_only["effective"]["verification"]["state"] == "off"
    assert cleared["saved"]["default_model"] is None
    assert cleared["saved"]["verification"] == {
        "enabled": None,
        "grader_model": None,
        "max_attempts": 2,
    }


def test_patch_noop_does_not_increment_revision_or_reset(client, settings_api, test_user_id):
    response = client.patch(
        "/settings",
        params={"user_id": test_user_id},
        json={"expected_revision": 0, "default_model": None},
    )

    assert response.status_code == 200
    assert response.json()["revision"] == 0
    assert settings_api["resets"] == []


@pytest.mark.parametrize(
    ("payload", "secret"),
    [
        ({"expected_revision": -1}, ""),
        ({"expected_revision": 0, "default_model": "bad-model"}, ""),
        ({"expected_revision": 0, "unknown": "do-not-leak"}, "do-not-leak"),
        (["do-not-leak-list"], "do-not-leak-list"),
    ],
)
def test_patch_validation_failures_are_secret_free_and_do_not_reset(
    client, settings_api, test_user_id, payload, secret
):
    response = client.patch("/settings", params={"user_id": test_user_id}, json=payload)

    assert response.status_code == 422
    if secret:
        assert secret not in response.text
    assert settings_api["resets"] == []


@pytest.mark.parametrize("error", [SettingsConfigurationError("secret-value"), SettingsWriteError("secret-value")])
def test_store_failures_are_controlled_and_do_not_reset(
    client, settings_api, monkeypatch, test_user_id, error
):
    store = settings_api["get_store"](test_user_id)
    monkeypatch.setattr(store, "patch", lambda patch: (_ for _ in ()).throw(error))

    response = client.patch(
        "/settings",
        params={"user_id": test_user_id},
        json={"expected_revision": 0, "default_model": "openai:gpt-4.1"},
    )

    assert response.status_code == 500
    assert response.json()["code"] == "configuration_error"
    assert "secret-value" not in response.text
    assert settings_api["resets"] == []


def test_resolution_failure_is_controlled_and_does_not_reset(
    client, settings_api, monkeypatch, test_user_id
):
    monkeypatch.setattr(
        settings_router,
        "resolve_effective_user_settings",
        lambda **kwargs: (_ for _ in ()).throw(
            settings_router.SettingsResolutionError("secret-resolution")
        ),
    )

    response = client.get("/settings", params={"user_id": test_user_id})

    assert response.status_code == 500
    assert response.json()["code"] == "configuration_error"
    assert "secret-resolution" not in response.text
    assert settings_api["resets"] == []


def test_patch_resolution_failure_leaves_durable_settings_unchanged(
    client, settings_api, monkeypatch, test_user_id
):
    store = settings_api["get_store"](test_user_id)
    store.patch(UserSettingsPatch(expected_revision=0, default_model="ollama:minimax-m2.5"))
    original_bytes = store.path.read_bytes()
    original_revision = store.load().revision
    settings_api["resets"].clear()
    monkeypatch.setattr(
        settings_router,
        "resolve_effective_user_settings",
        lambda **kwargs: (_ for _ in ()).throw(
            settings_router.SettingsResolutionError("preflight failed")
        ),
    )

    response = client.patch(
        "/settings",
        params={"user_id": test_user_id},
        json={"expected_revision": original_revision, "default_model": "openai:gpt-4.1"},
    )

    assert response.status_code == 500
    assert store.path.read_bytes() == original_bytes
    assert store.load().revision == original_revision
    assert settings_api["resets"] == []


def test_patch_response_after_commit_performs_no_store_prompt_or_catalog_io(
    client, settings_api, monkeypatch, test_user_id
):
    store = settings_api["get_store"](test_user_id)
    original_patch = store.patch

    def patch_then_disable_io(patch):
        mutation = original_patch(patch)
        monkeypatch.setattr(
            settings_router,
            "_catalog_snapshot",
            lambda: (_ for _ in ()).throw(AssertionError("post-commit catalog I/O")),
        )
        monkeypatch.setattr(
            store,
            "load",
            lambda: (_ for _ in ()).throw(AssertionError("post-commit store I/O")),
        )
        monkeypatch.setattr(
            store,
            "load_grader_prompt",
            lambda: (_ for _ in ()).throw(AssertionError("post-commit prompt I/O")),
        )
        return mutation

    monkeypatch.setattr(store, "patch", patch_then_disable_io)

    response = client.patch(
        "/settings",
        params={"user_id": test_user_id},
        json={"expected_revision": 0, "default_model": "openai:gpt-4.1"},
    )

    assert response.status_code == 200
    assert response.json()["revision"] == 1


def test_key_mutations_share_revision_preserve_settings_and_reset_only_on_change(
    client, settings_api, test_user_id
):
    store = settings_api["get_store"](test_user_id)
    store.patch(
        UserSettingsPatch(
            expected_revision=0,
            default_model="openai:gpt-4.1",
            verification=VerificationOverrides(enabled=False, max_attempts=2),
        )
    )
    settings_api["resets"].clear()

    stored = client.post(
        "/settings/api-keys",
        params={"user_id": test_user_id},
        json={"provider": "openai", "api_key": "same-key"},
    )
    same = client.post(
        "/settings/api-keys",
        params={"user_id": test_user_id},
        json={"provider": "openai", "api_key": "same-key"},
    )
    listed = client.get("/settings/api-keys", params={"user_id": test_user_id})
    deleted = client.delete(f"/settings/api-keys/openai?user_id={test_user_id}")
    absent = client.delete(f"/settings/api-keys/openai?user_id={test_user_id}")

    assert stored.json() == {"status": "stored", "provider": "openai", "revision": 2}
    assert same.json() == {"status": "stored", "provider": "openai", "revision": 2}
    assert listed.json() == {"openai": True}
    assert deleted.json() == {"status": "removed", "provider": "openai", "revision": 3}
    assert absent.json() == {"status": "removed", "provider": "openai", "revision": 3}
    current = store.load()
    assert current.default_model == "openai:gpt-4.1"
    assert current.verification == VerificationOverrides(enabled=False, max_attempts=2)
    assert settings_api["resets"] == [
        (test_user_id, "settings_changed"),
        (test_user_id, "settings_changed"),
    ]


def test_model_catalog_keeps_native_shape_with_effective_status_and_revision(
    client, settings_api, monkeypatch, test_user_id
):
    monkeypatch.setenv("OPENAI_API_KEY", "host-secret")
    store = settings_api["get_store"](test_user_id)
    store.patch(UserSettingsPatch(expected_revision=0, default_model="openai:gpt-4.1"))

    response = client.get("/settings/model-catalog", params={"user_id": test_user_id})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"revision", "default_model", "total_providers", "providers"}
    assert body["revision"] == 1
    assert body["default_model"] == "openai:gpt-4.1"
    openai = next(provider for provider in body["providers"] if provider["id"] == "openai")
    assert openai["key_source"] == "env"
    assert openai["has_key"] is True
    assert openai["models"][0]["key_source"] == "env"
    ollama = next(provider for provider in body["providers"] if provider["id"] == "ollama")
    assert ollama["key_source"] == "local"
    assert ollama["has_key"] is True
    assert ollama["models"][0]["key_source"] == "local"
    assert "host-secret" not in response.text


def test_sync_settings_routes_are_offloaded_by_fastapi() -> None:
    for handler in (
        settings_router.get_settings,
        settings_router.update_settings,
        settings_router.model_catalog,
        settings_router.list_api_keys,
        settings_router.set_api_key,
        settings_router.delete_api_key,
    ):
        assert not inspect.iscoroutinefunction(handler), handler.__name__

    assert inspect.iscoroutinefunction(settings_router.test_api_key)


def test_traversal_user_id_is_rejected_without_calling_store(client, monkeypatch):
    called = False

    def get_store(user_id: str) -> UserSettingsStore:
        nonlocal called
        called = True
        return UserSettingsStore(user_id)

    monkeypatch.setattr(settings_router, "_get_settings_store", get_store)

    response = client.get("/settings", params={"user_id": "../../escape"})

    assert response.status_code == 422
    assert "escape" not in response.text
    assert called is True


def test_write_failure_after_mutation_does_not_reset(client, settings_api, monkeypatch, test_user_id):
    store = settings_api["get_store"](test_user_id)
    monkeypatch.setattr(
        store,
        "set_provider_key",
        lambda provider, key: (_ for _ in ()).throw(SettingsWriteError("key-secret")),
    )

    response = client.post(
        "/settings/api-keys",
        params={"user_id": test_user_id},
        json={"provider": "openai", "api_key": "request-secret"},
    )

    assert response.status_code == 500
    assert "secret" not in response.text
    assert settings_api["resets"] == []
