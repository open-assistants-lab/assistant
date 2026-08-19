"""Tests for pure effective user settings resolution."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

import pytest

from src.config.user_settings import (
    EffectiveUserSettings,
    GraderPromptResponse,
    ProviderStatus,
    SavedUserSettings,
    VerificationOverrides,
)
from src.config.user_settings_service import (
    SettingsResolutionError,
    build_user_settings_response,
    resolve_effective_user_settings,
    resolve_provider_statuses,
)
from src.sdk.run_models import RubricAvailability, RubricUnavailableReason

_HASH = f"sha256:{'a' * 64}"
_DEFAULT_PROMPT = object()


def _prompt() -> GraderPromptResponse:
    return GraderPromptResponse(content="Grade this", source="seeded", content_hash=_HASH, revision=1)


def _statuses(source: str = "user") -> Mapping[str, ProviderStatus]:
    return {
        "openai": ProviderStatus(
            name="OpenAI",
            has_key=source != "none",
            key_configured_via_env=source == "env",
            key_source=source,  # type: ignore[arg-type]
        )
    }


def _resolve(
    *,
    saved: SavedUserSettings | None = None,
    prompt: GraderPromptResponse | None | object = _DEFAULT_PROMPT,
    host_default_model: str = "openai:host-chat",
    host_title_model: str | None = None,
    host_summarization_model: str | None = None,
    host_verification_enabled: bool = True,
    host_grader_model: str | None = "openai:host-grader",
    host_max_attempts: int = 2,
    provider_status: Mapping[str, ProviderStatus] | None = None,
    model_available: Callable[[str], bool] = lambda _model: True,
    provider_available: Callable[[str], bool] = lambda _provider: True,
) -> EffectiveUserSettings:
    return resolve_effective_user_settings(
        saved=saved or SavedUserSettings(),
        prompt=_prompt() if prompt is _DEFAULT_PROMPT else prompt,  # type: ignore[arg-type]
        host_default_model=host_default_model,
        host_title_model=host_title_model,
        host_summarization_model=host_summarization_model,
        host_verification_enabled=host_verification_enabled,
        host_grader_model=host_grader_model,
        host_max_attempts=host_max_attempts,
        provider_status=_statuses() if provider_status is None else provider_status,
        model_available=model_available,
        provider_available=provider_available,
    )


def test_saved_default_model_overrides_host_default() -> None:
    effective = _resolve(saved=SavedUserSettings(default_model="anthropic:saved-chat"))
    assert effective.default_model == "anthropic:saved-chat"


def test_host_default_model_is_canonicalized() -> None:
    assert _resolve(host_default_model=" openai : host-chat ").default_model == "openai:host-chat"


def test_saved_enabled_override_wins() -> None:
    saved = SavedUserSettings(verification=VerificationOverrides(enabled=False))
    assert _resolve(saved=saved, host_verification_enabled=True).verification.state is RubricAvailability.OFF


def test_host_enabled_is_used_when_unsaved() -> None:
    assert _resolve(host_verification_enabled=False).verification.state is RubricAvailability.OFF


def test_saved_grader_model_overrides_host() -> None:
    saved = SavedUserSettings(
        verification=VerificationOverrides(grader_model="openai:saved-grader")
    )
    assert _resolve(saved=saved).verification.grader_model == "openai:saved-grader"


def test_saved_title_model_overrides_host_and_default() -> None:
    saved = SavedUserSettings(title_model="openai:saved-title")
    effective = _resolve(saved=saved, host_title_model="openai:host-title")
    assert effective.title_model == "openai:saved-title"


def test_host_title_model_used_when_unsaved() -> None:
    effective = _resolve(host_title_model="openai:host-title")
    assert effective.title_model == "openai:host-title"


def test_title_model_defaults_to_default_model() -> None:
    effective = _resolve(host_default_model="openai:chat")
    assert effective.title_model == "openai:chat"


def test_saved_summarization_model_overrides_host_and_default() -> None:
    saved = SavedUserSettings(summarization_model="openai:saved-summary")
    effective = _resolve(saved=saved, host_summarization_model="openai:host-summary")
    assert effective.summarization_model == "openai:saved-summary"


def test_host_summarization_model_used_when_unsaved() -> None:
    effective = _resolve(host_summarization_model="openai:host-summary")
    assert effective.summarization_model == "openai:host-summary"


def test_summarization_model_defaults_to_default_model() -> None:
    effective = _resolve(host_default_model="openai:chat")
    assert effective.summarization_model == "openai:chat"


def test_host_grader_model_is_canonicalized() -> None:
    assert _resolve(host_grader_model=" openai : grader ").verification.grader_model == "openai:grader"


def test_blank_host_grader_defaults_to_effective_default_model() -> None:
    effective = _resolve(host_default_model="openai:chat", host_grader_model="  ")
    assert effective.verification.grader_model == "openai:chat"


def test_saved_max_attempts_overrides_host() -> None:
    saved = SavedUserSettings(verification=VerificationOverrides(max_attempts=3))
    assert _resolve(saved=saved, host_max_attempts=1).verification.max_attempts == 3


def test_host_max_attempts_is_used_when_unsaved() -> None:
    assert _resolve(host_max_attempts=3).verification.max_attempts == 3


def test_off_ignores_missing_prompt_invalid_model_and_credentials() -> None:
    saved = SavedUserSettings(verification=VerificationOverrides(enabled=False))
    effective = _resolve(
        saved=saved,
        prompt=None,
        host_grader_model="not-canonical",
        provider_status={},
    )
    assert effective.verification.state is RubricAvailability.OFF
    assert effective.verification.unavailable_reason is None
    assert effective.verification.grader_model is None


def test_off_retains_valid_diagnostics_without_callbacks() -> None:
    calls: list[str] = []
    saved = SavedUserSettings(verification=VerificationOverrides(enabled=False))

    def record_model(model: str) -> bool:
        calls.append(model)
        return True

    def record_provider(provider: str) -> bool:
        calls.append(provider)
        return True

    effective = _resolve(
        saved=saved,
        model_available=record_model,
        provider_available=record_provider,
    )
    assert effective.verification.grader_model == "openai:host-grader"
    assert effective.verification.grader_prompt_hash == _HASH
    # The role models (title/summarization) are catalog-validated, but the
    # grader is never evaluated while verification is off.
    assert calls == ["openai:host-chat", "openai:host-chat"]


def test_enabled_without_prompt_is_unavailable_before_callbacks() -> None:
    calls: list[str] = []

    def record_model(model: str) -> bool:
        calls.append(model)
        return True

    def record_provider(provider: str) -> bool:
        calls.append(provider)
        return True

    effective = _resolve(
        prompt=None,
        model_available=record_model,
        provider_available=record_provider,
    )
    assert effective.verification.unavailable_reason is RubricUnavailableReason.MISSING_PROMPT
    # Role-model catalog checks run first; the grader is never evaluated
    # once the missing prompt short-circuits the verification.
    assert calls == ["openai:host-chat", "openai:host-chat"]


def test_enabled_with_defensively_constructed_blank_prompt_is_unavailable() -> None:
    prompt = GraderPromptResponse.model_construct(
        content=" \n\t", source="customized", content_hash=_HASH, revision=1
    )

    effective = _resolve(prompt=prompt)

    assert effective.verification.state is RubricAvailability.UNAVAILABLE
    assert effective.verification.unavailable_reason is RubricUnavailableReason.MISSING_PROMPT
    assert effective.verification.grader_prompt_hash is None


def test_malformed_grader_is_unavailable_not_an_exception() -> None:
    effective = _resolve(host_grader_model="not-canonical")
    assert effective.verification.state is RubricAvailability.UNAVAILABLE
    assert effective.verification.unavailable_reason is RubricUnavailableReason.INVALID_GRADER_MODEL
    assert effective.verification.grader_model is None


def test_unknown_provider_is_unavailable() -> None:
    effective = _resolve(provider_available=lambda _provider: False)
    assert effective.verification.unavailable_reason is RubricUnavailableReason.PROVIDER_UNAVAILABLE


def test_unknown_model_is_unavailable() -> None:
    effective = _resolve(model_available=lambda _model: False)
    assert effective.verification.unavailable_reason is RubricUnavailableReason.INVALID_GRADER_MODEL


def test_missing_credentials_is_unavailable() -> None:
    effective = _resolve(provider_status=_statuses("none"))
    assert effective.verification.unavailable_reason is RubricUnavailableReason.MISSING_CREDENTIALS


def test_absent_provider_status_is_missing_credentials() -> None:
    effective = _resolve(provider_status={})
    assert effective.verification.unavailable_reason is RubricUnavailableReason.MISSING_CREDENTIALS


@pytest.mark.parametrize("source", ["user", "env", "hosted"])
def test_configured_credentials_enable_verification(source: str) -> None:
    assert _resolve(provider_status=_statuses(source)).verification.state is RubricAvailability.ON


@pytest.mark.parametrize("provider", ["ollama", "llamacpp"])
def test_local_providers_do_not_require_keys(provider: str) -> None:
    effective = _resolve(
        host_default_model=f"{provider}:chat",
        host_grader_model=f"{provider}:grader",
        provider_status={},
    )
    assert effective.verification.state is RubricAvailability.ON


def test_openrouter_slash_model_is_canonical() -> None:
    effective = _resolve(
        host_grader_model=" openrouter : anthropic/claude-sonnet-4 ",
        provider_status={
            "openrouter": ProviderStatus(name="OpenRouter", has_key=True, key_source="user")
        },
    )
    assert effective.verification.grader_model == "openrouter:anthropic/claude-sonnet-4"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"host_default_model": "secret-invalid-model"}, "default model"),
        ({"host_max_attempts": 4}, "max attempts"),
        ({"host_max_attempts": "2"}, "max attempts"),
    ],
)
def test_invalid_host_configuration_raises_secret_free_error(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(SettingsResolutionError, match=message) as caught:
        _resolve(**kwargs)  # type: ignore[arg-type]
    assert "secret-invalid-model" not in repr(caught.value)


def test_provider_status_user_key_has_precedence_over_environment() -> None:
    saved = SavedUserSettings(provider_keys={"openai": "known-secret"})
    statuses = resolve_provider_statuses(
        saved,
        [{"id": "openai", "name": "OpenAI", "env": ["CUSTOM_OPENAI_KEY"]}],
        {"OPENAI_API_KEY": "host-secret", "CUSTOM_OPENAI_KEY": "registry-secret"},
    )
    assert statuses["openai"].key_source == "user"
    assert "known-secret" not in repr(statuses)


def test_provider_status_uses_known_environment_name() -> None:
    statuses = resolve_provider_statuses(
        SavedUserSettings(), [{"id": "openrouter", "name": "OpenRouter"}],
        {"OPENROUTER_API_KEY": "secret"},
    )
    assert statuses["openrouter"].key_source == "env"


def test_provider_status_uses_registry_environment_names() -> None:
    statuses = resolve_provider_statuses(
        SavedUserSettings(), [{"id": "custom", "name": "Custom", "env": ["CUSTOM_KEY"]}],
        {"CUSTOM_KEY": "secret"},
    )
    assert statuses["custom"].key_source == "env"


def test_agnes_environment_is_hosted() -> None:
    statuses = resolve_provider_statuses(
        SavedUserSettings(), [{"id": "agnes", "name": "Agnes"}],
        {"AGNES_API_KEY": "secret"},
    )
    assert statuses["agnes"].key_source == "hosted"


def test_provider_status_none_when_no_key_exists() -> None:
    statuses = resolve_provider_statuses(
        SavedUserSettings(), [{"id": "openai", "name": "OpenAI"}], {}
    )
    assert statuses["openai"] == ProviderStatus(name="OpenAI", has_key=False, key_source="none")


@pytest.mark.parametrize("provider_id", ["ollama", "llamacpp"])
def test_local_provider_status_needs_no_credential(provider_id: str) -> None:
    statuses = resolve_provider_statuses(
        SavedUserSettings(), [{"id": provider_id, "name": "Local"}], {}
    )

    assert statuses[provider_id] == ProviderStatus(
        name="Local", has_key=True, key_source="local"
    )


def test_local_provider_saved_key_still_has_precedence() -> None:
    statuses = resolve_provider_statuses(
        SavedUserSettings(provider_keys={"ollama": "user-secret"}),
        [{"id": "ollama", "name": "Ollama"}],
        {"OLLAMA_API_KEY": "env-secret"},
    )

    assert statuses["ollama"].key_source == "user"


def test_malformed_provider_descriptors_are_skipped_and_first_collision_wins() -> None:
    statuses = resolve_provider_statuses(
        SavedUserSettings(),
        [
            {"id": " openai ", "name": "OpenAI"},
            {"id": "openai", "name": "Collision"},
            {"id": "", "name": "Blank"},
            {"id": 1, "name": "Wrong"},
            {"id": "bad-name", "name": []},
            {"id": "bad-env", "name": "Bad env", "env": ["OK", 1]},
        ],
        {},
    )
    assert list(statuses) == ["openai"]
    assert statuses["openai"].name == "OpenAI"


def test_response_contains_exact_saved_view_and_no_secrets() -> None:
    secret = "known-secret"
    saved = SavedUserSettings(
        revision=3,
        provider_keys={"openai": secret},
        default_model="openai:chat",
        verification=VerificationOverrides(enabled=True, max_attempts=3),
    )
    statuses = resolve_provider_statuses(saved, [{"id": "openai", "name": "OpenAI"}], {})
    response = build_user_settings_response(saved, _resolve(saved=saved), statuses)
    payload = json.loads(response.model_dump_json())
    assert payload["saved"] == {
        "default_model": "openai:chat",
        "title_model": None,
        "summarization_model": None,
        "verification": {"enabled": True, "grader_model": None, "max_attempts": 3},
    }
    assert payload["revision"] == 3
    assert "provider_keys" not in response.model_dump_json()
    assert secret not in response.model_dump_json()


def test_availability_callbacks_have_deterministic_order_only_after_prompt() -> None:
    calls: list[str] = []

    def record_provider(provider: str) -> bool:
        calls.append(f"provider:{provider}")
        return True

    def record_model(model: str) -> bool:
        calls.append(f"model:{model}")
        return True

    effective = _resolve(
        provider_available=record_provider,
        model_available=record_model,
    )
    assert effective.verification.state is RubricAvailability.ON
    # Deterministic order: role-model catalog checks, then the grader's
    # provider, then the grader model.
    assert calls == [
        "model:openai:host-chat",
        "model:openai:host-chat",
        "provider:openai",
        "model:openai:host-grader",
    ]
    assert effective.verification.grader_prompt_hash == _HASH


def test_stale_saved_title_model_falls_back_to_host() -> None:
    """A saved title model the catalog no longer knows degrades to the
    host value instead of 404ing at runtime."""
    saved = SavedUserSettings(title_model="openai:ghost-model")
    effective = _resolve(
        saved=saved,
        host_title_model="openai:host-title",
        model_available=lambda m: m in {"openai:host-title", "openai:host-chat"},
    )
    assert effective.title_model == "openai:host-title"


def test_all_stale_title_models_fall_back_to_default() -> None:
    """When the whole chain is stale, the (seeded) default is used."""
    saved = SavedUserSettings(title_model="openai:ghost")
    effective = _resolve(
        saved=saved,
        host_title_model="openai:ghost-host",
        model_available=lambda m: m == "openai:host-chat",
    )
    assert effective.title_model == "openai:host-chat"


def test_valid_saved_title_model_wins() -> None:
    """A saved title model that IS in the catalog is used unchanged."""
    saved = SavedUserSettings(title_model="openai:my-title")
    effective = _resolve(saved=saved, model_available=lambda m: m == "openai:my-title")
    assert effective.title_model == "openai:my-title"


def test_stale_saved_summarization_model_falls_back_to_host() -> None:
    saved = SavedUserSettings(summarization_model="openai:ghost-model")
    effective = _resolve(
        saved=saved,
        host_summarization_model="openai:host-summary",
        model_available=lambda m: m in {"openai:host-summary", "openai:host-chat"},
    )
    assert effective.summarization_model == "openai:host-summary"
