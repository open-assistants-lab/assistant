"""Tests for revisioned, atomic user settings persistence."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.config.user_settings import (
    GraderPromptResponse,
    GraderPromptUpdate,
    RevisionRequest,
    UserSettingsPatch,
    VerificationOverrides,
)
from src.config.user_settings_store import (
    RevisionConflict,
    SettingsConfigurationError,
    SettingsWriteError,
    UserSettingsStore,
)
from src.storage.paths import DataPaths

SECRET = "sk-test-super-secret"
SEED = "# Seed rubric\n\n- Check the answer.\n"


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


def _grader_store(
    tmp_path: Path,
    user_id: str = "alice",
    *,
    seed: str | None = SEED,
    legacy_default_rubric: str | None = "legacy rubric",
) -> UserSettingsStore:
    seed_path = tmp_path / "packaged" / "grader_prompt.md"
    if seed is not None:
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        seed_path.write_text(seed, encoding="utf-8")
    return UserSettingsStore(
        user_id,
        paths=_paths(tmp_path, user_id),
        legacy_path=tmp_path / "legacy" / user_id / "settings.json",
        grader_seed_path=seed_path,
        legacy_default_rubric=legacy_default_rubric,
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


def test_grader_prompt_contracts_are_immutable_exact_and_validate_schema() -> None:
    content = "  exact rubric\n\n"
    digest = f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"
    response = GraderPromptResponse(
        content=content,
        source="customized",
        content_hash=digest,
        revision=0,
    )
    update = GraderPromptUpdate(content=content, expected_revision=0)

    assert response.content == content
    assert update.content == content
    assert GraderPromptResponse.model_json_schema()["additionalProperties"] is False
    assert GraderPromptUpdate.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError):
        response.revision = 1
    with pytest.raises(ValidationError):
        GraderPromptResponse(
            content="x", source="customized", content_hash=f"sha256:{'A' * 64}", revision=0
        )
    with pytest.raises(ValidationError):
        RevisionRequest(expected_revision=-1)


@pytest.mark.parametrize("content", ["", " ", "\n\t"])
def test_grader_prompt_update_rejects_blank_content(content: str) -> None:
    with pytest.raises(ValidationError, match="content"):
        GraderPromptUpdate(content=content, expected_revision=0)


def test_load_grader_prompt_seeds_exact_content_without_settings_write(tmp_path: Path) -> None:
    store = _grader_store(tmp_path)

    response = store.load_grader_prompt()

    prompt_path = _paths(tmp_path).user_grader_prompt_path()
    assert prompt_path.read_text(encoding="utf-8") == SEED
    assert response == GraderPromptResponse(
        content=SEED,
        source="seeded",
        content_hash=f"sha256:{hashlib.sha256(SEED.encode()).hexdigest()}",
        revision=0,
    )
    assert not store.path.exists()


def test_default_packaged_seed_is_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = UserSettingsStore("alice", paths=_paths(tmp_path))

    response = store.load_grader_prompt()

    assert response.content.startswith("# Default Response Rubric\n")
    assert response.source == "seeded"


def test_missing_packaged_seed_uses_nonblank_legacy_default(tmp_path: Path) -> None:
    store = _grader_store(tmp_path, seed=None, legacy_default_rubric="  legacy exact\n")

    response = store.load_grader_prompt()

    assert response.content == "  legacy exact\n"
    assert response.source == "seeded"


@pytest.mark.parametrize("legacy", [None, "", " \n"])
def test_missing_packaged_and_legacy_default_raises_configuration_error(
    tmp_path: Path, legacy: str | None
) -> None:
    store = _grader_store(tmp_path, seed=None, legacy_default_rubric=legacy)

    with pytest.raises(SettingsConfigurationError, match="default grader prompt"):
        store.load_grader_prompt()


def test_grader_prompts_are_isolated_by_user(tmp_path: Path) -> None:
    alice = _grader_store(tmp_path, "alice")
    bob = _grader_store(tmp_path, "bob")

    alice.save_grader_prompt(GraderPromptUpdate(content="alice rubric\n", expected_revision=0))

    assert alice.load_grader_prompt().content == "alice rubric\n"
    assert bob.load_grader_prompt().content == SEED


def test_save_grader_prompt_preserves_exact_content_hash_and_increments_revision(
    tmp_path: Path,
) -> None:
    store = _grader_store(tmp_path)
    content = "  custom rubric\n\n"

    mutation = store.save_grader_prompt(
        GraderPromptUpdate(content=content, expected_revision=0)
    )

    assert mutation.changed is True
    assert mutation.response.content == content
    assert mutation.response.content_hash == f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"
    assert mutation.response.source == "customized"
    assert mutation.response.revision == 1
    assert store.load().revision == 1


def test_save_same_grader_prompt_is_noop(tmp_path: Path) -> None:
    store = _grader_store(tmp_path)
    store.load_grader_prompt()
    before = store.path.read_bytes() if store.path.exists() else None

    mutation = store.save_grader_prompt(
        GraderPromptUpdate(content=SEED, expected_revision=0)
    )

    assert mutation.changed is False
    assert mutation.response.revision == 0
    assert (store.path.read_bytes() if store.path.exists() else None) == before


def test_stale_grader_update_preserves_prompt_and_settings_bytes(tmp_path: Path) -> None:
    store = _grader_store(tmp_path)
    store.set_provider_key("openai", SECRET)
    prompt = store.load_grader_prompt()
    prompt_path = _paths(tmp_path).user_grader_prompt_path()
    prompt_before = prompt_path.read_bytes()
    settings_before = store.path.read_bytes()

    with pytest.raises(RevisionConflict):
        store.save_grader_prompt(
            GraderPromptUpdate(content="stale rubric", expected_revision=prompt.revision - 1)
        )

    assert prompt_path.read_bytes() == prompt_before
    assert store.path.read_bytes() == settings_before


def test_reset_grader_prompt_changes_once_then_is_noop(tmp_path: Path) -> None:
    store = _grader_store(tmp_path)
    changed = store.save_grader_prompt(
        GraderPromptUpdate(content="custom", expected_revision=0)
    )

    reset = store.reset_grader_prompt(RevisionRequest(expected_revision=1))
    noop = store.reset_grader_prompt(RevisionRequest(expected_revision=2))

    assert changed.changed is True
    assert reset.changed is True
    assert reset.response.content == SEED
    assert reset.response.source == "seeded"
    assert reset.response.revision == 2
    assert noop.changed is False
    assert noop.response.revision == 2


def test_stale_grader_reset_preserves_bytes(tmp_path: Path) -> None:
    store = _grader_store(tmp_path)
    store.save_grader_prompt(GraderPromptUpdate(content="custom", expected_revision=0))
    prompt_path = _paths(tmp_path).user_grader_prompt_path()
    prompt_before = prompt_path.read_bytes()
    settings_before = store.path.read_bytes()

    with pytest.raises(RevisionConflict):
        store.reset_grader_prompt(RevisionRequest(expected_revision=0))

    assert prompt_path.read_bytes() == prompt_before
    assert store.path.read_bytes() == settings_before


def test_prompt_write_failure_leaves_settings_and_prompt_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _grader_store(tmp_path)
    store.load_grader_prompt()
    prompt_path = _paths(tmp_path).user_grader_prompt_path()
    prompt_before = prompt_path.read_bytes()
    monkeypatch.setattr(
        store,
        "_atomic_write_prompt",
        lambda content: (_ for _ in ()).throw(SettingsWriteError("prompt write failed")),
    )

    with pytest.raises(SettingsWriteError):
        store.save_grader_prompt(GraderPromptUpdate(content=SECRET, expected_revision=0))

    assert prompt_path.read_bytes() == prompt_before
    assert not store.path.exists()


def test_settings_write_failure_rolls_back_existing_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _grader_store(tmp_path)
    store.set_provider_key("openai", SECRET)
    store.load_grader_prompt()
    prompt_path = _paths(tmp_path).user_grader_prompt_path()
    prompt_before = prompt_path.read_bytes()
    settings_before = store.path.read_bytes()
    monkeypatch.setattr(
        store,
        "_atomic_write",
        lambda settings: (_ for _ in ()).throw(SettingsWriteError("settings write failed")),
    )

    with pytest.raises(SettingsWriteError) as raised:
        store.save_grader_prompt(GraderPromptUpdate(content=SECRET, expected_revision=1))

    assert SECRET not in str(raised.value)
    assert prompt_path.read_bytes() == prompt_before
    assert store.path.read_bytes() == settings_before


def test_settings_write_failure_restores_initial_prompt_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _grader_store(tmp_path)
    prompt_path = _paths(tmp_path).user_grader_prompt_path()
    monkeypatch.setattr(
        store,
        "_atomic_write",
        lambda settings: (_ for _ in ()).throw(SettingsWriteError("settings write failed")),
    )

    with pytest.raises(SettingsWriteError):
        store.save_grader_prompt(GraderPromptUpdate(content="custom", expected_revision=0))

    assert not prompt_path.exists()
    assert not store.path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not supported")
def test_grader_prompt_is_mode_0600(tmp_path: Path) -> None:
    store = _grader_store(tmp_path)

    store.load_grader_prompt()

    assert stat.S_IMODE(_paths(tmp_path).user_grader_prompt_path().stat().st_mode) == 0o600


def test_grader_prompt_failure_cleans_unique_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import user_settings_store as store_module

    store = _grader_store(tmp_path)
    prompt_path = _paths(tmp_path).user_grader_prompt_path()
    real_replace = store_module.os.replace

    def fail_prompt_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        if Path(target) == prompt_path:
            raise OSError("boom")
        real_replace(source, target)

    monkeypatch.setattr(store_module.os, "replace", fail_prompt_replace)

    with pytest.raises(SettingsWriteError):
        store.load_grader_prompt()

    assert list(prompt_path.parent.glob(f".{prompt_path.name}.*.tmp")) == []


def test_packaged_seed_change_updates_hash_and_marks_old_seed_customized(tmp_path: Path) -> None:
    store = _grader_store(tmp_path, seed="first seed")
    first = store.load_grader_prompt()
    store.grader_seed_path.write_text("second seed", encoding="utf-8")
    new_user = UserSettingsStore(
        "bob",
        paths=_paths(tmp_path, "bob"),
        grader_seed_path=store.grader_seed_path,
        legacy_default_rubric=None,
    )

    second = store.load_grader_prompt()
    newly_seeded = new_user.load_grader_prompt()

    assert second.content == "first seed"
    assert second.content_hash == first.content_hash
    assert second.source == "customized"
    assert newly_seeded.content == "second seed"
    assert newly_seeded.content_hash != first.content_hash
    assert newly_seeded.source == "seeded"


def _fail_nth_fsync(
    monkeypatch: pytest.MonkeyPatch, nth_call: int
) -> None:
    from src.config import user_settings_store as store_module

    real_fsync = store_module.os.fsync
    calls = 0

    def fail_once(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == nth_call:
            raise OSError(errno.EIO, "durability failure")
        real_fsync(descriptor)

    monkeypatch.setattr(store_module.os, "fsync", fail_once)


@pytest.mark.parametrize("initially_existing", [False, True])
def test_post_replace_prompt_durability_failure_restores_both_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initially_existing: bool,
) -> None:
    store = _grader_store(tmp_path)
    prompt_path = _paths(tmp_path).user_grader_prompt_path()
    if initially_existing:
        store.set_provider_key("openai", SECRET)
        store.load_grader_prompt()
        prompt_path.chmod(0o640)
        store.path.chmod(0o640)
    prompt_before = prompt_path.read_bytes() if prompt_path.exists() else None
    settings_before = store.path.read_bytes() if store.path.exists() else None
    prompt_mode_before = stat.S_IMODE(prompt_path.stat().st_mode) if prompt_path.exists() else None
    settings_mode_before = stat.S_IMODE(store.path.stat().st_mode) if store.path.exists() else None
    expected_revision = store.load().revision
    _fail_nth_fsync(monkeypatch, 2)

    with pytest.raises(SettingsWriteError, match="durability confirmation failed"):
        store.save_grader_prompt(
            GraderPromptUpdate(content="replacement rubric", expected_revision=expected_revision)
        )

    assert (prompt_path.read_bytes() if prompt_path.exists() else None) == prompt_before
    assert (store.path.read_bytes() if store.path.exists() else None) == settings_before
    assert (stat.S_IMODE(prompt_path.stat().st_mode) if prompt_path.exists() else None) == prompt_mode_before
    assert (stat.S_IMODE(store.path.stat().st_mode) if store.path.exists() else None) == settings_mode_before
    assert store.load().revision == expected_revision


@pytest.mark.parametrize("initially_existing", [False, True])
def test_post_replace_settings_durability_failure_restores_both_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initially_existing: bool,
) -> None:
    store = _grader_store(tmp_path)
    prompt_path = _paths(tmp_path).user_grader_prompt_path()
    if initially_existing:
        store.set_provider_key("openai", SECRET)
        store.load_grader_prompt()
        prompt_path.chmod(0o640)
        store.path.chmod(0o640)
    prompt_before = prompt_path.read_bytes() if prompt_path.exists() else None
    settings_before = store.path.read_bytes() if store.path.exists() else None
    prompt_mode_before = stat.S_IMODE(prompt_path.stat().st_mode) if prompt_path.exists() else None
    settings_mode_before = stat.S_IMODE(store.path.stat().st_mode) if store.path.exists() else None
    expected_revision = store.load().revision
    _fail_nth_fsync(monkeypatch, 4)

    with pytest.raises(SettingsWriteError, match="durability confirmation failed"):
        store.save_grader_prompt(
            GraderPromptUpdate(content="replacement rubric", expected_revision=expected_revision)
        )

    assert (prompt_path.read_bytes() if prompt_path.exists() else None) == prompt_before
    assert (store.path.read_bytes() if store.path.exists() else None) == settings_before
    assert (stat.S_IMODE(prompt_path.stat().st_mode) if prompt_path.exists() else None) == prompt_mode_before
    assert (stat.S_IMODE(store.path.stat().st_mode) if store.path.exists() else None) == settings_mode_before
    assert store.load().revision == expected_revision


def test_default_seeding_post_replace_failure_restores_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _grader_store(tmp_path)
    prompt_path = _paths(tmp_path).user_grader_prompt_path()
    _fail_nth_fsync(monkeypatch, 2)

    with pytest.raises(SettingsWriteError, match="durability confirmation failed"):
        store.load_grader_prompt()

    assert not prompt_path.exists()
    assert not store.path.exists()


def test_omitted_fallback_ignores_uninitialized_settings_and_hostile_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import settings as settings_module

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "config.yaml").write_text(
        "verification:\n  default_rubric: hostile config rubric\n", encoding="utf-8"
    )
    (cwd / ".env").write_text(
        "VERIFICATION__DEFAULT_RUBRIC=conflicting cwd rubric\n", encoding="utf-8"
    )
    alice_paths = _paths(tmp_path)
    bob_paths = _paths(tmp_path, "bob")
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(settings_module, "_config", None)
    seed_path = tmp_path / "missing" / "grader_prompt.md"
    omitted = UserSettingsStore(
        "alice",
        paths=alice_paths,
        grader_seed_path=seed_path,
    )
    injected = UserSettingsStore(
        "bob",
        paths=bob_paths,
        grader_seed_path=seed_path,
        legacy_default_rubric="explicit fallback rubric",
    )

    with pytest.raises(SettingsConfigurationError, match="default grader prompt"):
        omitted.load_grader_prompt()

    assert injected.load_grader_prompt().content == "explicit fallback rubric"
    assert settings_module._config is None


def _journal_path(store: UserSettingsStore) -> Path:
    return store.path.with_name(".grader_prompt_transaction.json")


@pytest.mark.parametrize("crash_after", ["prompt", "settings"])
def test_next_load_recovers_crashed_grader_prompt_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_after: str,
) -> None:
    store = _grader_store(tmp_path)
    store.set_provider_key("openai", SECRET)
    store.load_grader_prompt()
    prompt_path = _paths(tmp_path).user_grader_prompt_path()
    prompt_before = prompt_path.read_bytes()
    settings_before = store.path.read_bytes()

    if crash_after == "prompt":
        monkeypatch.setattr(
            store,
            "_atomic_write",
            lambda settings: (_ for _ in ()).throw(SystemExit("simulated crash")),
        )
    else:
        monkeypatch.setattr(
            store,
            "_remove_journal",
            lambda: (_ for _ in ()).throw(SystemExit("simulated crash")),
            raising=False,
        )

    with pytest.raises(SystemExit, match="simulated crash"):
        store.save_grader_prompt(
            GraderPromptUpdate(content="replacement rubric", expected_revision=1)
        )

    assert _journal_path(store).exists()
    recovered = _grader_store(tmp_path)
    assert recovered.load().revision == 1
    assert prompt_path.read_bytes() == prompt_before
    assert recovered.path.read_bytes() == settings_before
    assert not _journal_path(recovered).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not supported")
def test_grader_prompt_transaction_journal_is_mode_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _grader_store(tmp_path)
    monkeypatch.setattr(
        store,
        "_atomic_write_prompt",
        lambda content: (_ for _ in ()).throw(SystemExit("simulated crash")),
    )

    with pytest.raises(SystemExit):
        store.save_grader_prompt(GraderPromptUpdate(content="custom", expected_revision=0))

    assert stat.S_IMODE(_journal_path(store).stat().st_mode) == 0o600


@pytest.mark.parametrize("insecure", [False, True])
def test_invalid_grader_prompt_transaction_journal_is_configuration_error(
    tmp_path: Path,
    insecure: bool,
) -> None:
    store = _grader_store(tmp_path)
    journal = _journal_path(store)
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(f'{{"invalid": "{SECRET}"}}', encoding="utf-8")
    journal.chmod(0o644 if insecure else 0o600)

    with pytest.raises(SettingsConfigurationError) as raised:
        store.load()

    assert SECRET not in str(raised.value)
    assert journal.exists()


def test_permanent_recovery_failure_leaves_journal_and_reports_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _grader_store(tmp_path)
    store.load_grader_prompt()
    monkeypatch.setattr(
        store,
        "_atomic_write",
        lambda settings: (_ for _ in ()).throw(SettingsWriteError("settings failed")),
    )
    monkeypatch.setattr(
        store,
        "_restore_snapshot",
        lambda path, snapshot: (_ for _ in ()).throw(OSError("restore failed")),
    )

    with pytest.raises(SettingsWriteError, match="recovery.*pending|manual") as raised:
        store.save_grader_prompt(GraderPromptUpdate(content=SECRET, expected_revision=0))

    assert SECRET not in str(raised.value)
    assert _journal_path(store).exists()


def test_successful_grader_prompt_transaction_removes_journal(tmp_path: Path) -> None:
    store = _grader_store(tmp_path)

    mutation = store.save_grader_prompt(
        GraderPromptUpdate(content="custom rubric", expected_revision=0)
    )

    assert mutation.changed is True
    assert not _journal_path(store).exists()
