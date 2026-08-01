"""Immutable contracts for persisted and effective user settings."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    Field,
    JsonValue,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

from src.sdk.run_models import (
    ContractModel,
    NonEmptyString,
    RubricAvailability,
    RubricUnavailableReason,
)

JSONScalar: TypeAlias = str | int | float | bool | None
FrozenJSONValue: TypeAlias = JSONScalar | tuple[object, ...] | Mapping[str, object]
CanonicalModel: TypeAlias = Annotated[
    str, Field(json_schema_extra={"pattern": r"^[^:/\s]+:\S+$"})
]
PromptHash: TypeAlias = Annotated[
    str, Field(json_schema_extra={"pattern": r"^sha256:[0-9a-f]{64}$"})
]
ProviderId: TypeAlias = Annotated[
    str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")
]


def canonical_model(value: str | None) -> str | None:
    """Normalize a nullable provider:model reference."""
    if value is None:
        return None
    provider, separator, model = value.partition(":")
    provider = provider.strip()
    model = model.strip()
    if (
        not separator
        or not provider
        or "/" in provider
        or any(character.isspace() for character in provider)
        or not model
        or any(character.isspace() for character in model)
    ):
        raise ValueError("model must use canonical provider:model syntax")
    return f"{provider}:{model}"


def _freeze_json(value: object) -> FrozenJSONValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("value must be JSON-compatible")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise ValueError("value must be JSON-compatible")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


class SettingsModel(ContractModel):
    """Base for immutable settings contracts with exact schemas."""


class VerificationOverrides(SettingsModel):
    enabled: bool | None = None
    grader_model: CanonicalModel | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=3)

    @field_validator("grader_model")
    @classmethod
    def _canonical_grader_model(cls, value: str | None) -> str | None:
        return canonical_model(value)


class SavedUserSettings(SettingsModel):
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    provider_keys: Mapping[ProviderId, str] = Field(default_factory=dict)
    default_model: CanonicalModel | None = None
    verification: VerificationOverrides = Field(default_factory=VerificationOverrides)

    @field_validator(
        "provider_keys", mode="plain", json_schema_input_type=dict[ProviderId, str]
    )
    @classmethod
    def _frozen_provider_keys(cls, value: object) -> Mapping[str, str]:
        if not isinstance(value, Mapping):
            raise ValueError("provider_keys must be an object")
        provider_keys: dict[str, str] = {}
        for provider, key in value.items():
            if not isinstance(provider, str) or not provider.strip():
                raise ValueError("provider_keys provider IDs must be nonempty strings")
            if not isinstance(key, str) or not key.strip():
                raise ValueError("provider_keys values must be nonempty strings")
            normalized_provider = provider.strip()
            if normalized_provider in provider_keys:
                raise ValueError(f"duplicate provider ID after trimming: {normalized_provider}")
            provider_keys[normalized_provider] = key
        return MappingProxyType(provider_keys)

    @field_validator("default_model")
    @classmethod
    def _canonical_default_model(cls, value: str | None) -> str | None:
        return canonical_model(value)

    @field_serializer("provider_keys")
    def _serialize_provider_keys(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)

    def __repr_args__(self) -> list[tuple[str | None, object]]:
        return [
            (name, {provider: "**********" for provider in value})
            if name == "provider_keys" and isinstance(value, Mapping)
            else (name, value)
            for name, value in super().__repr_args__()
        ]


class SavedUserSettingsView(SettingsModel):
    default_model: CanonicalModel | None = None
    verification: VerificationOverrides = Field(default_factory=VerificationOverrides)

    @field_validator("default_model")
    @classmethod
    def _canonical_default_model(cls, value: str | None) -> str | None:
        return canonical_model(value)


class EffectiveVerificationSettings(SettingsModel):
    state: RubricAvailability
    unavailable_reason: RubricUnavailableReason | None = None
    grader_model: CanonicalModel | None = None
    max_attempts: int = Field(default=1, ge=1, le=3)
    grader_prompt_hash: PromptHash | None = None

    @field_validator("grader_model")
    @classmethod
    def _canonical_grader_model(cls, value: str | None) -> str | None:
        return canonical_model(value)

    @field_validator("grader_prompt_hash")
    @classmethod
    def _canonical_prompt_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", value) is None:
            raise ValueError("grader_prompt_hash must be sha256 followed by 64 hexadecimal characters")
        return f"sha256:{value.removeprefix('sha256:').lower()}"

    @model_validator(mode="after")
    def _validate_state(self) -> EffectiveVerificationSettings:
        if self.state is RubricAvailability.UNAVAILABLE:
            if self.unavailable_reason is None:
                raise ValueError("unavailable_reason is required when state is unavailable")
        elif self.unavailable_reason is not None:
            raise ValueError("unavailable_reason is forbidden unless state is unavailable")

        if self.state is RubricAvailability.ON:
            if self.grader_model is None:
                raise ValueError("grader_model is required when state is on")
            if self.grader_prompt_hash is None:
                raise ValueError("grader_prompt_hash is required when state is on")
        return self


class EffectiveUserSettings(SettingsModel):
    default_model: CanonicalModel
    verification: EffectiveVerificationSettings

    @field_validator("default_model")
    @classmethod
    def _canonical_default_model(cls, value: str) -> str:
        normalized = canonical_model(value)
        if normalized is None:  # pragma: no cover - excluded by the field type
            raise ValueError("default_model is required")
        return normalized


class UserSettingsPatch(SettingsModel):
    expected_revision: int = Field(ge=0)
    default_model: CanonicalModel | None = None
    verification: VerificationOverrides | None = None

    @field_validator("default_model")
    @classmethod
    def _canonical_default_model(cls, value: str | None) -> str | None:
        return canonical_model(value)


class ProviderStatus(SettingsModel):
    name: NonEmptyString
    has_key: bool
    key_configured_via_env: bool = False
    key_source: Literal["none", "user", "env", "hosted"]

    @model_validator(mode="after")
    def _validate_key_metadata(self) -> ProviderStatus:
        expected = {
            "none": (False, False),
            "user": (True, False),
            "env": (True, True),
            "hosted": (True, False),
        }[self.key_source]
        if (self.has_key, self.key_configured_via_env) != expected:
            raise ValueError("has_key and key_configured_via_env must match key_source")
        return self


class UserSettingsResponse(SettingsModel):
    schema_version: Literal[1] = 1
    revision: int = Field(ge=0)
    saved: SavedUserSettingsView
    effective: EffectiveUserSettings
    provider_status: Mapping[ProviderId, ProviderStatus]

    @field_validator(
        "provider_status", mode="plain", json_schema_input_type=dict[ProviderId, ProviderStatus]
    )
    @classmethod
    def _frozen_provider_status(cls, value: object) -> Mapping[str, ProviderStatus]:
        if not isinstance(value, Mapping):
            raise ValueError("provider_status must be an object")
        statuses: dict[str, ProviderStatus] = {}
        for provider, status in value.items():
            if not isinstance(provider, str) or not provider.strip():
                raise ValueError("provider_status provider IDs must be nonempty strings")
            normalized_provider = provider.strip()
            if normalized_provider in statuses:
                raise ValueError(f"duplicate provider ID after trimming: {normalized_provider}")
            statuses[normalized_provider] = (
                status
                if isinstance(status, ProviderStatus)
                else ProviderStatus.model_validate(status)
            )
        return MappingProxyType(statuses)

    @field_serializer("provider_status")
    def _serialize_provider_status(
        self, value: Mapping[str, ProviderStatus]
    ) -> dict[str, ProviderStatus]:
        return dict(value)


class SettingsError(SettingsModel):
    code: Literal["revision_conflict", "validation_error", "configuration_error"]
    message: str
    details: FrozenJSONValue

    @field_validator("details", mode="plain", json_schema_input_type=JsonValue)
    @classmethod
    def _frozen_details(cls, value: object) -> FrozenJSONValue:
        return _freeze_json(value)

    @field_serializer("details")
    def _serialize_details(self, value: FrozenJSONValue) -> object:
        return _thaw_json(value)
