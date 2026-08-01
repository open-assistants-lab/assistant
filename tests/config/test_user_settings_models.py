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

_PROMPT_HASH = f"sha256:{'a' * 64}"


def _effective_verification(
    *, state: RubricAvailability = RubricAvailability.ON
) -> EffectiveVerificationSettings:
    return EffectiveVerificationSettings(
        state=state,
        grader_model="openai:gpt-5",
        max_attempts=2,
        grader_prompt_hash=_PROMPT_HASH,
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


def test_canonical_model_accepts_slashes_in_model_remainder_and_requires_provider() -> None:
    assert canonical_model(None) is None
    assert canonical_model(" openai : gpt-5 ") == "openai:gpt-5"
    assert (
        canonical_model(" openrouter : anthropic/claude-sonnet-4 ")
        == "openrouter:anthropic/claude-sonnet-4"
    )

    for invalid in (
        "anthropic/claude-sonnet-4",
        "openrouter:",
        ":anthropic/claude-sonnet-4",
        "open/router:model",
        "open router:model",
        "openrouter:anthropic/claude sonnet",
        "  ",
    ):
        with pytest.raises(ValueError):
            canonical_model(invalid)


def test_settings_imports_the_shared_canonical_model_contract() -> None:
    from src.config import user_settings
    from src.sdk import run_models

    assert user_settings.CanonicalModel is run_models.CanonicalModel
    model_schema = next(
        item
        for item in VerificationOverrides.model_json_schema()["properties"]["grader_model"][
            "anyOf"
        ]
        if item.get("type") == "string"
    )
    assert model_schema["pattern"] == r"^[^:/\s]+:\S+$"


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
            grader_prompt_hash=_PROMPT_HASH,
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


def test_saved_settings_repr_masks_provider_key_values_without_masking_json() -> None:
    secret = "known-provider-secret"
    settings = SavedUserSettings(provider_keys={"openai": secret})

    assert secret not in repr(settings)
    assert secret not in str(settings)
    assert json.loads(settings.model_dump_json())["provider_keys"] == {"openai": secret}


@pytest.mark.parametrize(
    "provider_keys",
    [{"": "secret"}, {"   ": "secret"}, {"openai": ""}, {"openai": "   "}],
)
def test_saved_provider_keys_reject_blank_ids_and_values(provider_keys: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="provider_keys"):
        SavedUserSettings(provider_keys=provider_keys)


def test_saved_provider_keys_reject_collisions_after_trimming() -> None:
    with pytest.raises(ValidationError, match="duplicate provider ID"):
        SavedUserSettings(provider_keys={"openai": "first", " openai ": "second"})


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
                grader_prompt_hash=_PROMPT_HASH if state is RubricAvailability.ON else None,
            )


def test_on_verification_requires_canonical_model_and_prompt_hash() -> None:
    with pytest.raises(ValidationError, match="grader_model"):
        EffectiveVerificationSettings(
            state=RubricAvailability.ON,
            grader_prompt_hash=_PROMPT_HASH,
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
            grader_prompt_hash=_PROMPT_HASH,
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
        grader_prompt_hash=_PROMPT_HASH,
    )
    assert off.grader_model == "openai:grader"


@pytest.mark.parametrize("state", [RubricAvailability.OFF, RubricAvailability.UNAVAILABLE])
@pytest.mark.parametrize(
    "invalid_hash",
    ["sha256:abc123", f"sha256:{'g' * 64}"],
)
def test_non_null_prompt_hash_is_exact_in_every_state(
    state: RubricAvailability, invalid_hash: str
) -> None:
    kwargs: dict[str, object] = {
        "state": state,
        "grader_prompt_hash": invalid_hash,
    }
    if state is RubricAvailability.UNAVAILABLE:
        kwargs["unavailable_reason"] = RubricUnavailableReason.MISSING_PROMPT

    with pytest.raises(ValidationError, match="grader_prompt_hash"):
        EffectiveVerificationSettings.model_validate(kwargs)


def test_prompt_hash_normalizes_uppercase_hex() -> None:
    settings = EffectiveVerificationSettings(
        state=RubricAvailability.OFF,
        grader_prompt_hash=f"sha256:{'ABCDEF' * 10}ABCD",
    )

    assert settings.grader_prompt_hash == f"sha256:{'abcdef' * 10}abcd"


def test_provider_status_rejects_unknown_key_source() -> None:
    with pytest.raises(ValidationError, match="key_source"):
        ProviderStatus(name="OpenAI", has_key=True, key_source="vault")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("key_source", "has_key", "key_configured_via_env"),
    [
        ("none", False, False),
        ("user", True, False),
        ("env", True, True),
        ("hosted", True, False),
    ],
)
def test_provider_status_accepts_consistent_key_metadata(
    key_source: str, has_key: bool, key_configured_via_env: bool
) -> None:
    status = ProviderStatus.model_validate(
        {
            "name": "OpenAI",
            "has_key": has_key,
            "key_configured_via_env": key_configured_via_env,
            "key_source": key_source,
        }
    )

    assert status.key_source == key_source


@pytest.mark.parametrize(
    ("key_source", "has_key", "key_configured_via_env"),
    [
        ("none", True, False),
        ("none", False, True),
        ("user", False, False),
        ("user", True, True),
        ("env", False, True),
        ("env", True, False),
        ("hosted", False, False),
        ("hosted", True, True),
    ],
)
def test_provider_status_rejects_contradictory_key_metadata(
    key_source: str, has_key: bool, key_configured_via_env: bool
) -> None:
    with pytest.raises(ValidationError, match="key_source"):
        ProviderStatus.model_validate(
            {
                "name": "OpenAI",
                "has_key": has_key,
                "key_configured_via_env": key_configured_via_env,
                "key_source": key_source,
            }
        )


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


def test_json_schema_describes_canonical_models_hashes_and_provider_ids() -> None:
    overrides_schema = VerificationOverrides.model_json_schema()["properties"]
    grader_model_schema = next(
        item for item in overrides_schema["grader_model"]["anyOf"] if item.get("type") == "string"
    )
    effective_schema = EffectiveVerificationSettings.model_json_schema()["properties"]
    hash_schema = next(
        item
        for item in effective_schema["grader_prompt_hash"]["anyOf"]
        if item.get("type") == "string"
    )
    saved_schema = SavedUserSettings.model_json_schema()["properties"]
    response_schema = UserSettingsResponse.model_json_schema()["properties"]

    assert grader_model_schema["pattern"] == r"^[^:/\s]+:\S+$"
    assert hash_schema["pattern"] == r"^sha256:[0-9a-f]{64}$"
    assert saved_schema["provider_keys"]["propertyNames"]["minLength"] == 1
    assert response_schema["provider_status"]["propertyNames"]["minLength"] == 1


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
