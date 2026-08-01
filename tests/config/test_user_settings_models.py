"""Tests for immutable user settings contracts."""

from __future__ import annotations

import json
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from src.config.user_settings import (
    EffectiveUserSettings,
    EffectiveVerificationSettings,
    ProviderStatus,
    SavedUserSettings,
    SavedUserSettingsView,
    SettingsError,
    UserSettingsPatch,
    UserSettingsResponse,
    VerificationOverrides,
    canonical_model,
)
from src.sdk.run_models import RubricAvailability, RubricUnavailableReason


def _effective_verification(
    *, state: RubricAvailability = RubricAvailability.ON
) -> EffectiveVerificationSettings:
    return EffectiveVerificationSettings(
        state=state,
        grader_model="openai:gpt-5",
        max_attempts=2,
        grader_prompt_hash="sha256:abc123",
    )


def _response(*, secret: str = "known-secret") -> UserSettingsResponse:
    saved = SavedUserSettings(
        revision=2,
        provider_keys={"openai": secret},
        default_model="openai:gpt-5",
        verification=VerificationOverrides(enabled=True),
    )
    return UserSettingsResponse(
        revision=saved.revision,
        saved=SavedUserSettingsView(
            default_model=saved.default_model,
            verification=saved.verification,
        ),
        effective=EffectiveUserSettings(
            default_model="openai:gpt-5",
            verification=_effective_verification(),
        ),
        provider_status={
            "openai": ProviderStatus(name="OpenAI", has_key=True, key_source="user")
        },
    )


def test_patch_preserves_explicit_null_and_false_field_sets() -> None:
    patch = UserSettingsPatch(
        expected_revision=0,
        default_model=None,
        verification=VerificationOverrides(enabled=False, grader_model=None),
    )

    assert patch.model_fields_set == {"expected_revision", "default_model", "verification"}
    assert patch.verification is not None
    assert patch.verification.enabled is False
    assert patch.verification.grader_model is None
    assert patch.verification.model_fields_set == {"enabled", "grader_model"}
    assert UserSettingsPatch(expected_revision=0).model_fields_set == {"expected_revision"}


def test_verification_rejects_more_than_three_attempts() -> None:
    with pytest.raises(ValidationError, match="max_attempts"):
        VerificationOverrides(max_attempts=4)

    with pytest.raises(ValidationError, match="max_attempts"):
        EffectiveVerificationSettings(
            state=RubricAvailability.OFF,
            max_attempts=4,
        )


def test_canonical_model_normalizes_nullable_values_and_rejects_slashes() -> None:
    assert canonical_model(None) is None
    assert canonical_model(" openai : gpt-5 ") == "openai:gpt-5"

    for invalid in ("openai/gpt-5", "openai:", ":gpt-5", "gpt-5", "  "):
        with pytest.raises(ValueError):
            canonical_model(invalid)


def test_canonical_model_normalization_applies_to_all_model_fields() -> None:
    overrides = VerificationOverrides(grader_model=" openai : grader ")
    saved = SavedUserSettings(default_model=" openai : chat ", verification=overrides)
    patch = UserSettingsPatch(
        expected_revision=0,
        default_model=" openai : next ",
        verification=VerificationOverrides(grader_model=" openai : next-grader "),
    )
    effective = EffectiveUserSettings(
        default_model=" openai : effective ",
        verification=EffectiveVerificationSettings(
            state=RubricAvailability.ON,
            grader_model=" openai : effective-grader ",
            grader_prompt_hash="sha256:prompt",
        ),
    )

    assert saved.default_model == "openai:chat"
    assert saved.verification.grader_model == "openai:grader"
    assert patch.default_model == "openai:next"
    assert patch.verification is not None
    assert patch.verification.grader_model == "openai:next-grader"
    assert effective.default_model == "openai:effective"
    assert effective.verification.grader_model == "openai:effective-grader"


def test_saved_settings_defaults_are_versioned_and_empty() -> None:
    settings = SavedUserSettings()

    assert settings.schema_version == 1
    assert settings.revision == 0
    assert settings.provider_keys == {}
    assert settings.default_model is None
    assert settings.verification == VerificationOverrides()


def test_saved_provider_keys_are_immutable_and_round_trip_as_json_object() -> None:
    settings = SavedUserSettings(provider_keys={" openai ": " secret "})

    assert settings.provider_keys == {"openai": " secret "}
    assert isinstance(settings.provider_keys, MappingProxyType)
    with pytest.raises(TypeError):
        settings.provider_keys["anthropic"] = "other"  # type: ignore[index]

    dumped = settings.model_dump_json()
    assert json.loads(dumped)["provider_keys"] == {"openai": " secret "}
    assert SavedUserSettings.model_validate_json(dumped) == settings


@pytest.mark.parametrize(
    "provider_keys",
    [{"": "secret"}, {"   ": "secret"}, {"openai": ""}, {"openai": "   "}],
)
def test_saved_provider_keys_reject_blank_ids_and_values(provider_keys: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="provider_keys"):
        SavedUserSettings(provider_keys=provider_keys)


def test_response_cannot_represent_provider_keys_or_secret_values() -> None:
    secret = "known-secret"
    response = _response(secret=secret)
    payload = response.model_dump_json()

    assert "provider_keys" not in payload
    assert secret not in payload
    assert "revision" not in type(response.saved).model_fields


def test_unavailable_reason_is_required_only_for_unavailable_state() -> None:
    with pytest.raises(ValidationError, match="unavailable_reason"):
        EffectiveVerificationSettings(state=RubricAvailability.UNAVAILABLE)

    unavailable = EffectiveVerificationSettings(
        state=RubricAvailability.UNAVAILABLE,
        unavailable_reason=RubricUnavailableReason.MISSING_PROMPT,
    )
    assert unavailable.unavailable_reason is RubricUnavailableReason.MISSING_PROMPT

    for state in (RubricAvailability.ON, RubricAvailability.OFF):
        with pytest.raises(ValidationError, match="unavailable_reason"):
            EffectiveVerificationSettings(
                state=state,
                unavailable_reason=RubricUnavailableReason.MISSING_PROMPT,
                grader_model="openai:grader" if state is RubricAvailability.ON else None,
                grader_prompt_hash="sha256:prompt" if state is RubricAvailability.ON else None,
            )


def test_on_verification_requires_canonical_model_and_prompt_hash() -> None:
    with pytest.raises(ValidationError, match="grader_model"):
        EffectiveVerificationSettings(
            state=RubricAvailability.ON,
            grader_prompt_hash="sha256:prompt",
        )
    with pytest.raises(ValidationError, match="grader_prompt_hash"):
        EffectiveVerificationSettings(
            state=RubricAvailability.ON,
            grader_model="openai:grader",
        )
    with pytest.raises(ValidationError):
        EffectiveVerificationSettings(
            state=RubricAvailability.ON,
            grader_model="openai/grader",
            grader_prompt_hash="sha256:prompt",
        )
    with pytest.raises(ValidationError, match="grader_prompt_hash"):
        EffectiveVerificationSettings(
            state=RubricAvailability.ON,
            grader_model="openai:grader",
            grader_prompt_hash="prompt",
        )
    with pytest.raises(ValidationError, match="grader_prompt_hash"):
        EffectiveVerificationSettings(
            state=RubricAvailability.ON,
            grader_model="openai:grader",
            grader_prompt_hash="sha256:   ",
        )

    off = EffectiveVerificationSettings(
        state=RubricAvailability.OFF,
        grader_model="openai:grader",
        grader_prompt_hash="sha256:prompt",
    )
    assert off.grader_model == "openai:grader"


def test_provider_status_rejects_unknown_key_source() -> None:
    with pytest.raises(ValidationError, match="key_source"):
        ProviderStatus(name="OpenAI", has_key=True, key_source="vault")  # type: ignore[arg-type]


def test_contracts_are_frozen_and_reject_extra_fields() -> None:
    status = ProviderStatus(name="OpenAI", has_key=False, key_source="none")
    with pytest.raises(ValidationError, match="frozen"):
        status.has_key = True

    with pytest.raises(ValidationError, match="extra"):
        ProviderStatus.model_validate(
            {"name": "OpenAI", "has_key": False, "key_source": "none", "extra": True}
        )


def test_response_provider_status_is_immutable() -> None:
    response = _response()

    assert isinstance(response.provider_status, MappingProxyType)
    with pytest.raises(TypeError):
        response.provider_status["other"] = ProviderStatus(  # type: ignore[index]
            name="Other", has_key=False, key_source="none"
        )


def test_response_provider_status_rejects_non_string_provider_id() -> None:
    payload: dict[str, object] = _response().model_dump()
    payload["provider_status"] = {
        1: {"name": "OpenAI", "has_key": True, "key_source": "user"}
    }

    with pytest.raises(ValidationError, match="provider IDs"):
        UserSettingsResponse.model_validate(payload)


@pytest.mark.parametrize("provider", ["", "   "])
def test_response_provider_status_rejects_blank_provider_id(provider: str) -> None:
    payload: dict[str, object] = _response().model_dump()
    payload["provider_status"] = {
        provider: {"name": "OpenAI", "has_key": True, "key_source": "user"}
    }

    with pytest.raises(ValidationError, match="provider IDs"):
        UserSettingsResponse.model_validate(payload)


def test_response_provider_status_trims_provider_ids_and_serializes_as_object() -> None:
    payload: dict[str, object] = _response().model_dump()
    payload["provider_status"] = {
        " openai ": {"name": "OpenAI", "has_key": True, "key_source": "user"}
    }

    response = UserSettingsResponse.model_validate(payload)

    assert response.provider_status == {"openai": response.provider_status["openai"]}
    assert list(json.loads(response.model_dump_json())["provider_status"]) == ["openai"]


def test_response_provider_status_rejects_collisions_after_trimming() -> None:
    payload: dict[str, object] = _response().model_dump()
    status = {"name": "OpenAI", "has_key": True, "key_source": "user"}
    payload["provider_status"] = {"openai": status, " openai ": status}

    with pytest.raises(ValidationError, match="duplicate provider ID"):
        UserSettingsResponse.model_validate(payload)


def test_settings_error_details_reject_non_json_values_and_round_trip() -> None:
    error = SettingsError(
        code="revision_conflict",
        message="Settings changed",
        details={"expected": 1, "actual": 2, "paths": ["default_model"]},
    )

    dumped = error.model_dump_json()
    assert SettingsError.model_validate_json(dumped) == error
    assert json.loads(dumped)["details"] == {
        "expected": 1,
        "actual": 2,
        "paths": ["default_model"],
    }

    with pytest.raises(ValidationError, match="JSON-compatible"):
        SettingsError(
            code="validation_error",
            message="Invalid details",
            details={"bad": object()},
        )
