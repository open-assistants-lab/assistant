from src.http.routers import settings as settings_router


async def test_update_default_model_resets_user_sdk_loops(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(settings_router, "_settings_path", lambda user_id: tmp_path / f"{user_id}.json")
    monkeypatch.setattr(
        "src.sdk.runner.reset_user_sdk_loops",
        lambda user_id, reason=None: calls.append((user_id, reason)),
    )

    result = await settings_router.update_settings(
        settings_router.UpdateSettingsRequest(default_model="openai:gpt-4.1"),
        user_id="u",
    )

    assert result == {"status": "updated"}
    assert calls == [("u", "settings_changed")]


async def test_set_api_key_resets_user_sdk_loops(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(settings_router, "_settings_path", lambda user_id: tmp_path / f"{user_id}.json")
    monkeypatch.setattr(
        "src.sdk.runner.reset_user_sdk_loops",
        lambda user_id, reason=None: calls.append((user_id, reason)),
    )

    result = await settings_router.set_api_key(
        settings_router.SetApiKeyRequest(provider="openai", api_key="key"),
        user_id="u",
    )

    assert result == {"status": "stored", "provider": "openai"}
    assert calls == [("u", "settings_changed")]


async def test_delete_api_key_resets_user_sdk_loops(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(settings_router, "_settings_path", lambda user_id: tmp_path / f"{user_id}.json")
    settings_router._write_settings("u", {"provider_keys": {"openai": "key"}, "default_model": None})
    monkeypatch.setattr(
        "src.sdk.runner.reset_user_sdk_loops",
        lambda user_id, reason=None: calls.append((user_id, reason)),
    )

    result = await settings_router.delete_api_key("openai", user_id="u")

    assert result == {"status": "removed", "provider": "openai"}
    assert calls == [("u", "settings_changed")]
