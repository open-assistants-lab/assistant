"""Tests for revisioned, atomic user settings persistence."""

from __future__ import annotations

import errno
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from src.config.user_settings import UserSettingsPatch, VerificationOverrides
from src.config.user_settings_store import (
    RevisionConflict,
    SettingsConfigurationError,
    SettingsWriteError,
    UserSettingsStore,
)
from src.storage.paths import DataPaths

SECRET = "sk-test-super-secret"


def _paths(tmp_path: Path, user_id: str = "alice") -> DataPaths:
    return DataPaths(
        deployment="solo",
        data_path=str(tmp_path / "project"),
        ea_root=str(tmp_path / "home"),
        user_id=user_id,
    )


def _store(tmp_path: Path, user_id: str = "alice") -> UserSettingsStore:
    paths = _paths(tmp_path, user_id)
    return UserSettingsStore(
        user_id,
        paths=paths,
        legacy_path=tmp_path / "legacy" / user_id / "settings.json",
    )


def _write_json(path: Path, payload: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode() + b"\n"
    path.write_bytes(encoded)
    return encoded


def _canonical(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "revision": 2,
        "provider_keys": {"openai": "canonical-key"},
        "default_model": "openai:gpt-5",
        "verification": {"enabled": True, "grader_model": "openai:grader", "max_attempts": 2},
    }
    payload.update(overrides)
    return payload


def test_missing_files_return_immutable_revision_zero_defaults_without_write(tmp_path: Path) -> None:
    store = _store(tmp_path)

    settings = store.load()

    assert settings.revision == 0
    assert settings.provider_keys == {}
    assert not store.path.exists()
    with pytest.raises(Exception, match="frozen"):
        settings.revision = 3


def test_properties_expose_injected_paths(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.path == _paths(tmp_path).user_settings_path()
    assert store.legacy_path == tmp_path / "legacy" / "alice" / "settings.json"


def test_grader_seed_path_is_injectable_and_has_deterministic_packaged_default(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    injected_seed = tmp_path / "packaged" / "grader.md"

    injected = UserSettingsStore(
        "alice",
        paths=paths,
        legacy_path=tmp_path / "legacy.json",
        grader_seed_path=injected_seed,
    )
    default_first = UserSettingsStore("alice", paths=paths)
    default_second = UserSettingsStore("alice", paths=paths)

    assert injected.grader_seed_path == injected_seed
    assert default_first.grader_seed_path == default_second.grader_seed_path
    assert default_first.grader_seed_path.is_absolute()
    assert default_first.grader_seed_path.parts[-3:] == (
        "seeds",
        "prompts",
        "grader_prompt.md",
    )


def test_legacy_migration_preserves_values_and_renames_source(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(
        store.legacy_path,
        {"provider_keys": {"openai": SECRET}, "default_model": "openai:gpt-4.1"},
    )

    settings = store.load()

    assert settings.provider_keys == {"openai": SECRET}
    assert settings.default_model == "openai:gpt-4.1"
    assert settings.revision == 0
    assert store.path.exists()
    assert not store.legacy_path.exists()
    assert store.legacy_path.with_name("settings.json.migrated").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not supported")
def test_migrated_legacy_file_is_restricted_to_owner(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.legacy_path, {"provider_keys": {"openai": SECRET}})
    store.legacy_path.chmod(0o644)

    store.load()

    migrated = store.legacy_path.with_name("settings.json.migrated")
    assert stat.S_IMODE(migrated.stat().st_mode) == 0o600


def test_case_insensitive_legacy_alias_is_treated_only_as_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import user_settings_store as store_module

    paths = _paths(tmp_path)
    canonical_path = paths.user_settings_path()
    _write_json(canonical_path, _canonical())
    legacy_alias = canonical_path.with_name(canonical_path.name.upper())
    if not legacy_alias.exists():
        _write_json(legacy_alias, _canonical())
    warnings: list[dict[str, Any]] = []
    monkeypatch.setattr(store_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        store_module.logger,
        "warning",
        lambda event, data, user_id="default_user": warnings.append(data),
    )
    store = UserSettingsStore("alice", paths=paths, legacy_path=legacy_alias)

    settings = store.load()

    assert settings.revision == 2
    assert canonical_path.exists()
    assert not canonical_path.with_name(f"{legacy_alias.name}.migrated").exists()
    assert warnings == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not supported")
def test_unversioned_aliased_legacy_is_normalized_in_place_without_rename(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    shared_path = paths.user_settings_path()
    _write_json(
        shared_path,
        {
            "provider_keys": {"anthropic": SECRET},
            "default_model": "anthropic/claude-sonnet",
        },
    )
    shared_path.chmod(0o644)
    store = UserSettingsStore("alice", paths=paths, legacy_path=shared_path)

    settings = store.load()

    persisted = json.loads(shared_path.read_text(encoding="utf-8"))
    assert settings.default_model == "anthropic:claude-sonnet"
    assert settings.provider_keys == {"anthropic": SECRET}
    assert persisted["schema_version"] == 1
    assert persisted["revision"] == 0
    assert persisted["default_model"] == "anthropic:claude-sonnet"
    assert stat.S_IMODE(shared_path.stat().st_mode) == 0o600
    assert not shared_path.with_name("settings.json.migrated").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not supported")
def test_versioned_aliased_canonical_is_chmodded_without_rename(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    shared_path = paths.user_settings_path()
    original = _write_json(shared_path, _canonical(provider_keys={"openai": SECRET}))
    shared_path.chmod(0o644)
    store = UserSettingsStore("alice", paths=paths, legacy_path=shared_path)

    settings = store.load()

    assert settings.revision == 2
    assert shared_path.read_bytes() == original
    assert stat.S_IMODE(shared_path.stat().st_mode) == 0o600
    assert not shared_path.with_name("settings.json.migrated").exists()


def test_canonical_and_legacy_merge_keys_with_canonical_precedence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.path, _canonical(provider_keys={"openai": "new", "gemini": "g"}))
    _write_json(
        store.legacy_path,
        {"provider_keys": {"openai": "old", "anthropic": "a"}, "default_model": "openai:old"},
    )

    settings = store.load()

    assert settings.provider_keys == {"openai": "new", "gemini": "g", "anthropic": "a"}
    assert settings.default_model == "openai:gpt-5"
    assert settings.revision == 2
    assert settings.verification.enabled is True
    assert json.loads(store.path.read_text(encoding="utf-8"))["provider_keys"] == {
        "anthropic": "a",
        "gemini": "g",
        "openai": "new",
    }
    assert store.legacy_path.with_name("settings.json.migrated").exists()


def test_legacy_default_is_used_when_canonical_field_was_omitted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    canonical = _canonical()
    del canonical["default_model"]
    _write_json(store.path, canonical)
    _write_json(store.legacy_path, {"provider_keys": {}, "default_model": "anthropic:claude"})

    assert store.load().default_model == "anthropic:claude"


@pytest.mark.parametrize(
    ("legacy_model", "expected"),
    [
        (" openai : gpt-5 ", "openai:gpt-5"),
        ("anthropic/claude-sonnet", "anthropic:claude-sonnet"),
        ("llama3.2", "ollama:llama3.2"),
    ],
)
def test_legacy_models_normalize_to_canonical_ids(
    tmp_path: Path, legacy_model: str, expected: str
) -> None:
    store = _store(tmp_path)
    _write_json(
        store.legacy_path,
        {"provider_keys": {"openai": SECRET}, "default_model": legacy_model},
    )

    settings = store.load()

    assert settings.default_model == expected
    assert settings.provider_keys == {"openai": SECRET}


def test_invalid_legacy_model_preserves_keys_and_logs_only_redacted_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import user_settings_store as store_module

    store = _store(tmp_path)
    invalid_model = f"bad provider/{SECRET}"
    _write_json(
        store.legacy_path,
        {"provider_keys": {"openai": SECRET}, "default_model": invalid_model},
    )
    calls: list[tuple[str, dict[str, Any], str]] = []
    monkeypatch.setattr(
        store_module.logger,
        "warning",
        lambda event, data, user_id="default_user": calls.append((event, data, user_id)),
    )

    settings = store.load()

    assert settings.provider_keys == {"openai": SECRET}
    assert settings.default_model is None
    assert len(calls) == 1
    assert set(calls[0][1]) == {"model_type", "model_length", "model_syntax"}
    assert SECRET not in repr(calls)
    assert invalid_model not in repr(calls)


def test_explicit_canonical_null_default_wins_over_legacy(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.path, _canonical(default_model=None))
    _write_json(store.legacy_path, {"provider_keys": {}, "default_model": "anthropic:claude"})

    assert store.load().default_model is None


def test_malformed_canonical_raises_typed_secret_free_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(f'{{"provider_keys": {{"openai": "{SECRET}"}},', encoding="utf-8")

    with pytest.raises(SettingsConfigurationError) as raised:
        store.load()

    assert SECRET not in str(raised.value)
    assert SECRET not in repr(raised.value)
    assert raised.value.__cause__ is None


def test_unsupported_canonical_schema_raises_configuration_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.path, _canonical(schema_version=2))

    with pytest.raises(SettingsConfigurationError, match="[Cc]anonical"):
        store.load()


def test_malformed_legacy_without_canonical_raises_and_is_untouched(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = b'{"provider_keys": '
    store.legacy_path.parent.mkdir(parents=True, exist_ok=True)
    store.legacy_path.write_bytes(original)

    with pytest.raises(SettingsConfigurationError, match="[Ll]egacy"):
        store.load()

    assert store.legacy_path.read_bytes() == original
    assert not store.path.exists()


def test_malformed_legacy_with_valid_canonical_is_ignored_and_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import user_settings_store as store_module

    store = _store(tmp_path)
    _write_json(store.path, _canonical())
    store.legacy_path.parent.mkdir(parents=True, exist_ok=True)
    store.legacy_path.write_text(f'{{"provider_keys": {{"x": "{SECRET}"}}', encoding="utf-8")
    calls: list[tuple[str, dict[str, Any], str]] = []
    monkeypatch.setattr(
        store_module.logger,
        "warning",
        lambda event, data, user_id="default_user": calls.append((event, data, user_id)),
    )

    settings = store.load()

    assert settings.revision == 2
    assert store.legacy_path.exists()
    assert calls
    assert SECRET not in repr(calls)


def test_patch_changes_only_supplied_field_and_increments_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.path, _canonical())

    mutation = store.patch(UserSettingsPatch(expected_revision=2, default_model="anthropic:new"))

    assert mutation.changed is True
    assert mutation.settings.revision == 3
    assert mutation.settings.default_model == "anthropic:new"
    assert mutation.settings.provider_keys == {"openai": "canonical-key"}
    assert mutation.settings.verification.enabled is True


def test_stale_patch_conflict_has_fields_and_preserves_bytes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = _write_json(store.path, _canonical())

    with pytest.raises(RevisionConflict) as raised:
        store.patch(UserSettingsPatch(expected_revision=1, default_model="anthropic:new"))

    assert raised.value.expected == 1
    assert raised.value.actual == 2
    assert store.path.read_bytes() == before


def test_patch_explicit_null_clears_default_while_omission_preserves_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.path, _canonical())

    omitted = store.patch(UserSettingsPatch(expected_revision=2))
    cleared = store.patch(UserSettingsPatch(expected_revision=2, default_model=None))

    assert omitted.changed is False
    assert omitted.settings.default_model == "openai:gpt-5"
    assert cleared.changed is True
    assert cleared.settings.default_model is None


def test_nested_patch_false_null_and_omission_are_distinct(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.path, _canonical())

    mutation = store.patch(
        UserSettingsPatch(
            expected_revision=2,
            verification=VerificationOverrides(enabled=False, grader_model=None),
        )
    )

    assert mutation.settings.verification.enabled is False
    assert mutation.settings.verification.grader_model is None
    assert mutation.settings.verification.max_attempts == 2


def test_explicit_null_verification_resets_all_overrides(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.path, _canonical())

    mutation = store.patch(UserSettingsPatch(expected_revision=2, verification=None))

    assert mutation.settings.verification == VerificationOverrides()


def test_semantic_noop_does_not_write_or_increment(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = _write_json(store.path, _canonical())

    mutation = store.patch(UserSettingsPatch(expected_revision=2, default_model="openai:gpt-5"))

    assert mutation.changed is False
    assert mutation.settings.revision == 2
    assert store.path.read_bytes() == before


def test_set_provider_key_preserves_unrelated_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.path, _canonical())

    mutation = store.set_provider_key("anthropic", SECRET)

    assert mutation.changed is True
    assert mutation.settings.revision == 3
    assert mutation.settings.provider_keys == {"openai": "canonical-key", "anthropic": SECRET}
    assert mutation.settings.default_model == "openai:gpt-5"
    assert mutation.settings.verification.enabled is True


def test_setting_same_provider_key_is_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.path, _canonical())

    mutation = store.set_provider_key("openai", "canonical-key")

    assert mutation.changed is False
    assert mutation.settings.revision == 2


def test_delete_provider_key_increments_and_preserves_other_keys(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.path, _canonical(provider_keys={"openai": "o", "anthropic": "a"}))

    mutation = store.delete_provider_key("openai")

    assert mutation.changed is True
    assert mutation.settings.revision == 3
    assert mutation.settings.provider_keys == {"anthropic": "a"}


def test_delete_absent_provider_key_is_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.path, _canonical())

    mutation = store.delete_provider_key("anthropic")

    assert mutation.changed is False
    assert mutation.settings.revision == 2


def test_get_provider_key_returns_value_or_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write_json(store.path, _canonical(provider_keys={"openai": SECRET}))

    assert store.get_provider_key("openai") == SECRET
    assert store.get_provider_key("anthropic") is None


@pytest.mark.parametrize(("provider", "key"), [("", "value"), ("   ", "value"), ("ok", "")])
def test_provider_mutations_reuse_model_validation(
    tmp_path: Path, provider: str, key: str
) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError):
        store.set_provider_key(provider, key)


def test_users_are_isolated(tmp_path: Path) -> None:
    alice = _store(tmp_path, "alice")
    bob = _store(tmp_path, "bob")

    alice.set_provider_key("openai", SECRET)

    assert alice.get_provider_key("openai") == SECRET
    assert bob.get_provider_key("openai") is None
    assert alice.path != bob.path


def test_traversal_user_id_is_rejected_before_legacy_interpolation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="user_id"):
        UserSettingsStore("../escape", paths=_paths(tmp_path))

    assert not (tmp_path / "escape").exists()


def test_two_store_instances_with_same_revision_allow_exactly_one_thread_to_win(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    legacy = tmp_path / "legacy.json"
    first = UserSettingsStore("alice", paths=paths, legacy_path=legacy)
    second = UserSettingsStore("alice", paths=paths, legacy_path=legacy)
    _write_json(first.path, _canonical(revision=0))

    def update(store: UserSettingsStore, model: str) -> str:
        try:
            store.patch(UserSettingsPatch(expected_revision=0, default_model=model))
        except RevisionConflict:
            return "conflict"
        return "changed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda args: update(*args), [(first, "openai:a"), (second, "openai:b")])
        )

    assert sorted(results) == ["changed", "conflict"]
    assert first.load().revision == 1


def test_serialization_failure_preserves_old_bytes_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import user_settings_store as store_module

    store = _store(tmp_path)
    before = _write_json(store.path, _canonical())
    monkeypatch.setattr(store_module.json, "dump", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom")))

    with pytest.raises(SettingsWriteError) as raised:
        store.set_provider_key("anthropic", SECRET)

    assert SECRET not in str(raised.value)
    assert raised.value.__cause__ is None
    assert store.path.read_bytes() == before
    assert list(store.path.parent.glob(f".{store.path.name}.*.tmp")) == []


def test_replace_failure_preserves_old_bytes_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import user_settings_store as store_module

    store = _store(tmp_path)
    before = _write_json(store.path, _canonical())
    monkeypatch.setattr(store_module.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("boom")))

    with pytest.raises(SettingsWriteError):
        store.set_provider_key("anthropic", SECRET)

    assert store.path.read_bytes() == before
    assert list(store.path.parent.glob(f".{store.path.name}.*.tmp")) == []


def test_atomic_write_uses_unique_temp_in_target_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import user_settings_store as store_module

    store = _store(tmp_path)
    observed: list[Path] = []
    real_mkstemp = store_module.tempfile.mkstemp

    def recording_mkstemp(*, prefix: str | None = None, suffix: str | None = None, dir: str | None = None) -> tuple[int, str]:
        descriptor, temporary_name = real_mkstemp(prefix=prefix, suffix=suffix, dir=dir)
        observed.append(Path(temporary_name))
        return descriptor, temporary_name

    monkeypatch.setattr(store_module.tempfile, "mkstemp", recording_mkstemp)

    store.set_provider_key("openai", SECRET)
    store.set_provider_key("anthropic", "second-secret")

    assert len(observed) == 2
    assert observed[0] != observed[1]
    assert {path.parent for path in observed} == {store.path.parent}
    assert all(
        path.name.startswith(".settings.json.") and path.name.endswith(".tmp")
        for path in observed
    )


def test_canonical_file_mode_is_owner_only(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.set_provider_key("openai", SECRET)

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_parent_close_failure_after_replace_does_not_report_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import user_settings_store as store_module

    store = _store(tmp_path)
    monkeypatch.setattr(
        store_module.os,
        "close",
        lambda descriptor: (_ for _ in ()).throw(OSError("unsupported directory close")),
    )

    mutation = store.set_provider_key("openai", SECRET)

    assert mutation.changed is True
    assert store.load().provider_keys == {"openai": SECRET}


def test_unsupported_parent_fsync_error_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import user_settings_store as store_module

    store = _store(tmp_path)
    real_fsync = store_module.os.fsync
    calls = 0

    def unsupported_on_parent(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EINVAL, "directory fsync unsupported")
        real_fsync(descriptor)

    monkeypatch.setattr(store_module.os, "fsync", unsupported_on_parent)

    mutation = store.set_provider_key("openai", SECRET)

    assert mutation.changed is True
    assert mutation.settings.revision == 1


def test_parent_fsync_eio_surfaces_durability_error_and_persisted_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import user_settings_store as store_module

    store = _store(tmp_path)
    _write_json(store.path, _canonical(revision=0, provider_keys={"openai": SECRET}))
    real_fsync = store_module.os.fsync
    calls = 0

    def fail_parent_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EIO, "durability failure")
        real_fsync(descriptor)

    monkeypatch.setattr(store_module.os, "fsync", fail_parent_fsync)

    with pytest.raises(SettingsWriteError, match="durability confirmation failed") as raised:
        store.patch(UserSettingsPatch(expected_revision=0, default_model="openai:new"))

    assert SECRET not in str(raised.value)
    assert store.load().revision == 1
    with pytest.raises(RevisionConflict):
        store.patch(UserSettingsPatch(expected_revision=0, default_model="openai:new"))


def test_parent_open_eio_surfaces_durability_error_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import user_settings_store as store_module

    store = _store(tmp_path)
    real_open = store_module.os.open

    def fail_parent_open(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        if Path(path) == store.path.parent:
            raise OSError(errno.EIO, "durability failure")
        return real_open(path, flags, mode)

    monkeypatch.setattr(store_module.os, "open", fail_parent_open)

    with pytest.raises(SettingsWriteError, match="durability confirmation failed"):
        store.set_provider_key("openai", SECRET)

    assert store.load().provider_keys == {"openai": SECRET}


def test_exceptions_and_mutation_repr_do_not_expose_secrets(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mutation = store.set_provider_key("openai", SECRET)
    conflict = RevisionConflict(expected=0, actual=1)

    assert SECRET not in repr(mutation)
    assert SECRET not in str(mutation)
    assert SECRET not in repr(conflict)
    assert SECRET not in str(conflict)
