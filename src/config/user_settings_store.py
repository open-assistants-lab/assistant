"""Revisioned, atomic persistence for immutable user settings."""

from __future__ import annotations

import base64
import binascii
import errno
import hashlib
import io
import json as json
import os as os
import platform as platform
import stat
import tempfile as tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from typing import Any

from pydantic import ValidationError

from src.app_logging import get_logger
from src.config.user_settings import (
    GraderPromptResponse,
    GraderPromptUpdate,
    RevisionRequest,
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


@dataclass(frozen=True)
class GraderPromptMutation:
    response: GraderPromptResponse
    changed: bool


@dataclass(frozen=True)
class _FileSnapshot:
    existed: bool
    content: bytes | None
    mode: int | None


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
        legacy_default_rubric: str | None = None,
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
        self._grader_prompt_path = resolved_paths.user_grader_prompt_path()
        self._journal_path = self._path.with_name(".grader_prompt_transaction.json")
        self._legacy_default_rubric = legacy_default_rubric
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

    def load_grader_prompt(self) -> GraderPromptResponse:
        with self._lock:
            settings = self._load_locked()
            application_default = self._application_default_grader_prompt()
            if not self._grader_prompt_path.exists():
                snapshot = self._snapshot_file(self._grader_prompt_path)
                try:
                    self._atomic_write_prompt(application_default)
                except SettingsWriteError:
                    self._restore_snapshot(self._grader_prompt_path, snapshot)
                    raise
            content = self._read_grader_prompt()
            return self._grader_prompt_response(content, application_default, settings.revision)

    def save_grader_prompt(self, update: GraderPromptUpdate) -> GraderPromptMutation:
        with self._lock:
            return self._mutate_grader_prompt(update.content, update.expected_revision)

    def reset_grader_prompt(self, request: RevisionRequest) -> GraderPromptMutation:
        with self._lock:
            self._recover_pending_transaction()
            return self._mutate_grader_prompt(
                self._application_default_grader_prompt(), request.expected_revision
            )

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

    def _mutate_grader_prompt(
        self, content: str, expected_revision: int
    ) -> GraderPromptMutation:
        current_settings = self._load_locked()
        if expected_revision != current_settings.revision:
            raise RevisionConflict(expected_revision, current_settings.revision)
        if not content.strip():
            raise ValueError("Grader prompt content must not be blank")

        application_default = self._application_default_grader_prompt()
        existed = self._grader_prompt_path.exists()
        previous = self._read_grader_prompt() if existed else application_default
        if content == previous:
            return GraderPromptMutation(
                response=self._grader_prompt_response(
                    previous, application_default, current_settings.revision
                ),
                changed=False,
            )

        settings_payload = current_settings.model_dump(mode="json")
        settings_payload["revision"] = current_settings.revision + 1
        changed_settings = SavedUserSettings.model_validate(settings_payload)
        prompt_snapshot = self._snapshot_file(self._grader_prompt_path)
        settings_snapshot = self._snapshot_file(self._path)
        try:
            self._write_journal(prompt_snapshot, settings_snapshot)
            self._atomic_write_prompt(content)
            self._atomic_write(changed_settings)
            self._remove_journal()
        except SettingsWriteError as original:
            if self._journal_exists():
                try:
                    self._recover_pending_transaction()
                except (SettingsConfigurationError, SettingsWriteError):
                    raise SettingsWriteError(
                        "Grader prompt transaction recovery is pending; manual recovery may be required"
                    ) from None
            raise original
        except Exception:
            if self._journal_exists():
                try:
                    self._recover_pending_transaction()
                except (SettingsConfigurationError, SettingsWriteError):
                    raise SettingsWriteError(
                        "Grader prompt transaction recovery is pending; manual recovery may be required"
                    ) from None
            raise
        return GraderPromptMutation(
            response=self._grader_prompt_response(
                content, application_default, changed_settings.revision
            ),
            changed=True,
        )

    def _application_default_grader_prompt(self) -> str:
        try:
            content = self._grader_seed_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            content = ""
        if content.strip():
            return content
        if isinstance(self._legacy_default_rubric, str) and self._legacy_default_rubric.strip():
            return self._legacy_default_rubric
        raise SettingsConfigurationError("No default grader prompt is configured")

    def _read_grader_prompt(self) -> str:
        try:
            return self._grader_prompt_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise SettingsConfigurationError("User grader prompt is invalid") from None

    @staticmethod
    def _grader_prompt_response(
        content: str, application_default: str, revision: int
    ) -> GraderPromptResponse:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return GraderPromptResponse(
            content=content,
            source="seeded" if content == application_default else "customized",
            content_hash=f"sha256:{digest}",
            revision=revision,
        )

    @staticmethod
    def _snapshot_file(path: Path) -> _FileSnapshot:
        if not path.exists():
            return _FileSnapshot(existed=False, content=None, mode=None)
        return _FileSnapshot(
            existed=True,
            content=path.read_bytes(),
            mode=stat.S_IMODE(path.stat().st_mode),
        )

    def _restore_snapshot(self, path: Path, snapshot: _FileSnapshot) -> None:
        descriptor: int | None = None
        temporary_path: Path | None = None
        try:
            if not snapshot.existed:
                path.unlink(missing_ok=True)
                self._best_effort_fsync_parent(path)
                return

            assert snapshot.content is not None
            assert snapshot.mode is not None
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.rollback.",
                suffix=".tmp",
                dir=str(path.parent),
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, snapshot.mode)
            file = os.fdopen(descriptor, "wb")
            descriptor = None
            with file:
                file.write(snapshot.content)
                file.flush()
                try:
                    os.fsync(file.fileno())
                except OSError:
                    pass
            os.replace(temporary_path, path)
            temporary_path = None
            self._best_effort_fsync_parent(path)
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

    def _best_effort_fsync_parent(self, path: Path) -> None:
        try:
            self._fsync_parent(path, "rollback")
        except SettingsWriteError:
            pass

    @staticmethod
    def _snapshot_payload(snapshot: _FileSnapshot) -> dict[str, object]:
        return {
            "existed": snapshot.existed,
            "content": (
                base64.b64encode(snapshot.content).decode("ascii")
                if snapshot.content is not None
                else None
            ),
            "mode": snapshot.mode,
        }

    @staticmethod
    def _snapshot_from_payload(payload: object) -> _FileSnapshot:
        if not isinstance(payload, dict) or set(payload) != {"existed", "content", "mode"}:
            raise ValueError
        existed = payload["existed"]
        content = payload["content"]
        mode = payload["mode"]
        if not isinstance(existed, bool):
            raise ValueError
        if not existed:
            if content is not None or mode is not None:
                raise ValueError
            return _FileSnapshot(existed=False, content=None, mode=None)
        if not isinstance(content, str) or not isinstance(mode, int) or isinstance(mode, bool):
            raise ValueError
        if not 0 <= mode <= 0o7777:
            raise ValueError
        decoded = base64.b64decode(content.encode("ascii"), validate=True)
        return _FileSnapshot(existed=True, content=decoded, mode=mode)

    def _write_journal(
        self, prompt: _FileSnapshot, settings: _FileSnapshot
    ) -> None:
        payload = {
            "version": 1,
            "prompt": self._snapshot_payload(prompt),
            "settings": self._snapshot_payload(settings),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        self._atomic_write_text(
            self._journal_path, encoded, "grader prompt transaction journal"
        )

    def _read_journal(self) -> tuple[_FileSnapshot, _FileSnapshot]:
        try:
            journal_stat = self._journal_path.lstat()
            if not stat.S_ISREG(journal_stat.st_mode) or self._journal_path.is_symlink():
                raise ValueError
            if os.name != "nt" and stat.S_IMODE(journal_stat.st_mode) != 0o600:
                raise ValueError
            payload = json.loads(self._journal_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "version",
                "prompt",
                "settings",
            }:
                raise ValueError
            if payload["version"] != 1:
                raise ValueError
            return (
                self._snapshot_from_payload(payload["prompt"]),
                self._snapshot_from_payload(payload["settings"]),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, binascii.Error):
            raise SettingsConfigurationError(
                "Grader prompt transaction journal is invalid or insecure"
            ) from None

    def _recover_pending_transaction(self) -> None:
        if not self._journal_exists():
            return
        prompt_snapshot, settings_snapshot = self._read_journal()
        failed = False
        for path, snapshot in (
            (self._grader_prompt_path, prompt_snapshot),
            (self._path, settings_snapshot),
        ):
            try:
                self._restore_snapshot(path, snapshot)
            except Exception:
                failed = True
        if failed:
            raise SettingsWriteError(
                "Grader prompt transaction recovery is pending; manual recovery may be required"
            )
        try:
            self._remove_journal()
        except SettingsWriteError:
            raise SettingsWriteError(
                "Grader prompt transaction recovery is pending; manual recovery may be required"
            ) from None

    def _remove_journal(self) -> None:
        try:
            journal_content = self._journal_path.read_text(encoding="utf-8")
            self._journal_path.unlink()
            self._fsync_parent(self._journal_path, "grader prompt transaction journal")
        except Exception:
            if not self._journal_exists() and "journal_content" in locals():
                try:
                    self._atomic_write_text(
                        self._journal_path,
                        journal_content,
                        "grader prompt transaction journal",
                    )
                except SettingsWriteError:
                    pass
            raise SettingsWriteError(
                "Unable to finalize grader prompt transaction journal"
            ) from None

    def _journal_exists(self) -> bool:
        return os.path.lexists(self._journal_path)

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
        self._recover_pending_transaction()
        canonical: SavedUserSettings | None = None
        canonical_raw: dict[str, Any] | None = None
        canonical_exists = self._path.exists()
        legacy_exists = self._legacy_path.exists()
        if canonical_exists and legacy_exists and self._paths_alias():
            return self._load_aliased_settings()
        if canonical_exists:
            canonical, canonical_raw = self._read_canonical()

        if not legacy_exists:
            return canonical or SavedUserSettings()

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

    def _load_aliased_settings(self) -> SavedUserSettings:
        raw = self._read_json(self._path, source="settings")
        if not isinstance(raw, dict):
            raise SettingsConfigurationError("User settings are invalid")
        if "schema_version" in raw:
            settings = self._validate_configuration(raw, source="canonical")
            self._secure_canonical_file()
            return settings

        settings = self._parse_legacy(raw)
        self._atomic_write(settings)
        return settings

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
        return self._parse_legacy(raw)

    def _parse_legacy(self, raw: object) -> SavedUserSettings:
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

    def _secure_canonical_file(self) -> None:
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            raise SettingsWriteError("Unable to secure user settings") from None

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
        buffer = io.StringIO()
        try:
            json.dump(settings.model_dump(mode="json"), buffer, indent=2, sort_keys=True)
        except Exception:
            raise SettingsWriteError("Unable to persist user settings") from None
        payload = buffer.getvalue() + "\n"
        self._atomic_write_text(self._path, payload, "user settings")

    def _atomic_write_prompt(self, content: str) -> None:
        self._atomic_write_text(self._grader_prompt_path, content, "grader prompt")

    def _atomic_write_text(self, path: Path, content: str, subject: str) -> None:
        descriptor: int | None = None
        temporary_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=str(path.parent),
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            file = os.fdopen(descriptor, "wb")
            descriptor = None
            with file:
                file.write(content.encode("utf-8"))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
            self._fsync_parent(path, subject)
        except SettingsWriteError:
            raise
        except Exception:
            raise SettingsWriteError(f"Unable to persist {subject}") from None
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

    def _fsync_parent(self, path: Path | None = None, subject: str = "user settings") -> None:
        target = path or self._path
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(target.parent, flags)
        except OSError as exc:
            if exc.errno in _UNSUPPORTED_DURABILITY_ERRNOS:
                return
            raise SettingsWriteError(
                f"{subject.capitalize()} were replaced, but durability confirmation failed"
            ) from None
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED_DURABILITY_ERRNOS:
                raise SettingsWriteError(
                    f"{subject.capitalize()} were replaced, but durability confirmation failed"
                ) from None
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
