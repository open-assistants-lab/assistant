"""Revisioned, atomic persistence for immutable user settings."""

from __future__ import annotations

import errno
import json as json
import os as os
import platform as platform
import tempfile as tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from typing import Any

from pydantic import ValidationError

from src.app_logging import get_logger
from src.config.user_settings import (
    SavedUserSettings,
    UserSettingsPatch,
    VerificationOverrides,
)
from src.storage.paths import DataPaths

logger = get_logger()
_DEFAULT_GRADER_SEED_PATH = (
    Path(__file__).resolve().parents[2] / "seeds" / "prompts" / "grader_prompt.md"
)
_UNSUPPORTED_DURABILITY_ERRNOS = {
    errno.EBADF,
    errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.EINVAL),
}


class UserSettingsStoreError(Exception):
    """Base error for user settings persistence."""


class RevisionConflict(UserSettingsStoreError):  # noqa: N818 - public API name
    """Raised when a patch targets an obsolete settings revision."""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Settings revision conflict: expected {expected}, actual {actual}")


class SettingsConfigurationError(UserSettingsStoreError):
    """Raised when persisted settings cannot be safely interpreted."""


class SettingsWriteError(UserSettingsStoreError):
    """Raised when settings cannot be atomically persisted."""


@dataclass(frozen=True)
class SettingsMutation:
    settings: SavedUserSettings
    changed: bool


_LOCKS_GUARD = Lock()
_PATH_LOCKS: dict[Path, RLock] = {}


def _lock_for(path: Path) -> RLock:
    canonical_path = path.resolve(strict=False)
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(canonical_path, RLock())


class UserSettingsStore:
    """Load and mutate one user's settings with revision and process-local locking."""

    def __init__(
        self,
        user_id: str,
        paths: DataPaths | None = None,
        legacy_path: Path | None = None,
        grader_seed_path: Path | None = None,
    ) -> None:
        resolved_paths = paths or DataPaths(user_id=user_id)
        if resolved_paths.user_id != user_id:
            raise ValueError("Invalid user_id: it must match the supplied DataPaths")

        self._user_id = resolved_paths.user_id
        self._path = resolved_paths.user_settings_path()
        self._legacy_path = legacy_path or (
            resolved_paths.base / "users" / self._user_id / "settings.json"
        )
        self._grader_seed_path = grader_seed_path or _DEFAULT_GRADER_SEED_PATH
        self._lock = _lock_for(self._path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def legacy_path(self) -> Path:
        return self._legacy_path

    @property
    def grader_seed_path(self) -> Path:
        return self._grader_seed_path

    def load(self) -> SavedUserSettings:
        with self._lock:
            return self._load_locked()

    def patch(self, patch: UserSettingsPatch) -> SettingsMutation:
        with self._lock:
            current = self._load_locked()
            if patch.expected_revision != current.revision:
                raise RevisionConflict(patch.expected_revision, current.revision)

            payload = current.model_dump(mode="json")
            if "default_model" in patch.model_fields_set:
                payload["default_model"] = patch.default_model
            if "verification" in patch.model_fields_set:
                payload["verification"] = self._patched_verification(
                    current.verification, patch.verification
                )

            candidate = SavedUserSettings.model_validate(payload)
            if candidate == current:
                return SettingsMutation(settings=current, changed=False)

            payload["revision"] = current.revision + 1
            changed = SavedUserSettings.model_validate(payload)
            self._atomic_write(changed)
            return SettingsMutation(settings=changed, changed=True)

    def set_provider_key(self, provider: str, key: str) -> SettingsMutation:
        normalized_provider, validated_key = self._validated_provider_key(provider, key)
        with self._lock:
            current = self._load_locked()
            if current.provider_keys.get(normalized_provider) == validated_key:
                return SettingsMutation(settings=current, changed=False)

            provider_keys = dict(current.provider_keys)
            provider_keys[normalized_provider] = validated_key
            return self._replace_provider_keys(current, provider_keys)

    def delete_provider_key(self, provider: str) -> SettingsMutation:
        normalized_provider, _ = self._validated_provider_key(provider, "validation-placeholder")
        with self._lock:
            current = self._load_locked()
            if normalized_provider not in current.provider_keys:
                return SettingsMutation(settings=current, changed=False)

            provider_keys = dict(current.provider_keys)
            del provider_keys[normalized_provider]
            return self._replace_provider_keys(current, provider_keys)

    def get_provider_key(self, provider: str) -> str | None:
        normalized_provider, _ = self._validated_provider_key(provider, "validation-placeholder")
        with self._lock:
            return self._load_locked().provider_keys.get(normalized_provider)

    @staticmethod
    def _patched_verification(
        current: VerificationOverrides, patch: VerificationOverrides | None
    ) -> dict[str, Any]:
        if patch is None:
            return VerificationOverrides().model_dump(mode="json")
        payload = current.model_dump(mode="json")
        for field in patch.model_fields_set:
            payload[field] = getattr(patch, field)
        return payload

    @staticmethod
    def _validated_provider_key(provider: str, key: str) -> tuple[str, str]:
        try:
            validated = SavedUserSettings(provider_keys={provider: key})
        except (ValidationError, ValueError):
            raise ValueError("Invalid provider ID or provider key") from None
        return next(iter(validated.provider_keys.items()))

    def _replace_provider_keys(
        self, current: SavedUserSettings, provider_keys: dict[str, str]
    ) -> SettingsMutation:
        payload = current.model_dump(mode="json")
        payload["provider_keys"] = provider_keys
        payload["revision"] = current.revision + 1
        changed = SavedUserSettings.model_validate(payload)
        self._atomic_write(changed)
        return SettingsMutation(settings=changed, changed=True)

    def _load_locked(self) -> SavedUserSettings:
        canonical: SavedUserSettings | None = None
        canonical_raw: dict[str, Any] | None = None
        canonical_exists = self._path.exists()
        legacy_exists = self._legacy_path.exists()
        if canonical_exists:
            canonical, canonical_raw = self._read_canonical()

        if not legacy_exists:
            return canonical or SavedUserSettings()
        if canonical is not None and self._paths_alias():
            return canonical

        try:
            legacy = self._read_legacy()
        except SettingsConfigurationError:
            if canonical is None:
                raise
            logger.warning(
                "user_settings.legacy_invalid",
                {"message": "Legacy user settings could not be migrated"},
                user_id=self._user_id,
            )
            return canonical

        if canonical is None:
            self._atomic_write(legacy)
            self._rename_legacy()
            return legacy

        assert canonical_raw is not None
        merged_payload = canonical.model_dump(mode="json")
        merged_payload["provider_keys"] = {
            **dict(legacy.provider_keys),
            **dict(canonical.provider_keys),
        }
        if "default_model" not in canonical_raw:
            merged_payload["default_model"] = legacy.default_model
        merged = self._validate_configuration(merged_payload, source="merged")
        if merged != canonical:
            self._atomic_write(merged)
        self._rename_legacy()
        return merged

    def _paths_alias(self) -> bool:
        try:
            if self._path.samefile(self._legacy_path):
                return True
        except OSError:
            pass

        canonical = os.path.normcase(str(self._path.resolve(strict=False)))
        legacy = os.path.normcase(str(self._legacy_path.resolve(strict=False)))
        if platform.system() == "Darwin":
            canonical = canonical.casefold()
            legacy = legacy.casefold()
        return canonical == legacy

    def _read_canonical(self) -> tuple[SavedUserSettings, dict[str, Any]]:
        raw = self._read_json(self._path, source="canonical")
        if not isinstance(raw, dict):
            raise SettingsConfigurationError("Canonical user settings are invalid")
        return self._validate_configuration(raw, source="canonical"), raw

    def _read_legacy(self) -> SavedUserSettings:
        raw = self._read_json(self._legacy_path, source="legacy")
        if not isinstance(raw, dict) or not set(raw).issubset({"provider_keys", "default_model"}):
            raise SettingsConfigurationError("Legacy user settings are invalid")
        payload = {
            "schema_version": 1,
            "revision": 0,
            "provider_keys": raw.get("provider_keys", {}),
            "default_model": None,
            "verification": {},
        }
        settings = self._validate_configuration(payload, source="legacy")
        legacy_model = raw.get("default_model")
        if legacy_model is None:
            return settings

        payload["default_model"] = self._normalize_legacy_model(legacy_model)
        try:
            return self._validate_configuration(payload, source="legacy model")
        except SettingsConfigurationError:
            logger.warning(
                "user_settings.legacy_model_invalid",
                self._legacy_model_metadata(legacy_model),
                user_id=self._user_id,
            )
            return settings

    @staticmethod
    def _normalize_legacy_model(value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if ":" in normalized:
            return normalized
        if "/" in normalized:
            provider, model = normalized.split("/", 1)
            return f"{provider.strip()}:{model.strip()}"
        return f"ollama:{normalized}"

    @staticmethod
    def _legacy_model_metadata(value: object) -> dict[str, object]:
        if not isinstance(value, str):
            syntax = "non_string"
            length = None
        else:
            syntax = "colon" if ":" in value else "slash" if "/" in value else "bare"
            length = len(value)
        return {
            "model_type": type(value).__name__,
            "model_length": length,
            "model_syntax": syntax,
        }

    @staticmethod
    def _read_json(path: Path, *, source: str) -> object:
        try:
            with path.open(encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            raise SettingsConfigurationError(
                f"{source.capitalize()} user settings are invalid"
            ) from None

    @staticmethod
    def _validate_configuration(payload: object, *, source: str) -> SavedUserSettings:
        try:
            return SavedUserSettings.model_validate(payload)
        except (ValidationError, ValueError, TypeError):
            raise SettingsConfigurationError(
                f"{source.capitalize()} user settings are invalid"
            ) from None

    def _rename_legacy(self) -> None:
        migrated_path = self._legacy_path.with_name(f"{self._legacy_path.name}.migrated")
        try:
            os.chmod(self._legacy_path, 0o600)
            os.replace(self._legacy_path, migrated_path)
        except OSError:
            raise SettingsWriteError("Unable to finish user settings migration") from None

    def _atomic_write(self, settings: SavedUserSettings) -> None:
        descriptor: int | None = None
        temporary_path: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=str(self._path.parent),
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            file = os.fdopen(descriptor, "w", encoding="utf-8")
            descriptor = None
            with file:
                json.dump(settings.model_dump(mode="json"), file, indent=2, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, self._path)
            temporary_path = None
            self._fsync_parent()
        except SettingsWriteError:
            raise
        except Exception:
            raise SettingsWriteError("Unable to persist user settings") from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _fsync_parent(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(self._path.parent, flags)
        except OSError as exc:
            if exc.errno in _UNSUPPORTED_DURABILITY_ERRNOS:
                return
            raise SettingsWriteError(
                "User settings were replaced, but durability confirmation failed"
            ) from None
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED_DURABILITY_ERRNOS:
                raise SettingsWriteError(
                    "User settings were replaced, but durability confirmation failed"
                ) from None
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
