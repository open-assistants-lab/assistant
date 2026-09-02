"""Tests for deployment-aware data paths."""

from __future__ import annotations

import pytest

from src.storage.paths import DataPaths


def test_workspace_dir_rejects_traversal_workspace_id(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    with pytest.raises(ValueError, match="Invalid workspace_id"):
        DataPaths(workspace_id="../../escaped")

    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize("user_id", ["alice/../bob", "alice:bob"])
def test_user_id_rejects_aliasing_and_separators(tmp_path, user_id):
    with pytest.raises(ValueError, match="Invalid user_id"):
        DataPaths(data_path=str(tmp_path / "data"), user_id=user_id)


@pytest.mark.parametrize("workspace_id", ["sales/../support", "sales:support"])
def test_workspace_id_rejects_aliasing_and_separators(tmp_path, monkeypatch, workspace_id):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    with pytest.raises(ValueError, match="Invalid workspace_id"):
        DataPaths(workspace_id=workspace_id)


def test_workspace_dir_accepts_normal_workspace_ids(monkeypatch, tmp_path):
    # Hermetic against suite order: tests/api's session fixture may leave
    # DEPLOYMENT_DATA_ROOT set (its teardown runs only at session end), and
    # the settings singleton may hold that value. Stub get_settings so the
    # Path.home() default is what's actually under test.
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    class _Deployment:
        data_root = ""

    class _Settings:
        deployment = _Deployment()
        data_path = "data"

    import src.storage.paths as _paths_mod
    monkeypatch.setattr(_paths_mod, "get_settings", lambda: _Settings())

    for workspace_id in ("personal", "project-x"):
        paths = DataPaths(workspace_id=workspace_id)

        workspace_dir = paths.workspace_skills_dir().parent

        assert workspace_dir == tmp_path / "Assistant"
        assert workspace_dir.exists()


def test_workspace_path_helpers_are_user_level_aliases(tmp_path):
    paths = DataPaths(data_root=str(tmp_path / "data"), workspace_id="project-x")

    assert paths.workspace_files_dir() == paths.files_dir()
    assert paths.workspace_skills_dir() == paths.user_skills_dir()
    assert paths.workspace_subagents_dir() == paths.user_subagents_dir()
    assert paths.workspace_memory_dir() == paths.user_memory_dir()
    assert paths.versions_dir() == paths.root / ".versions"
    assert paths.workspace_conversation_path() == paths.conversation_dir() / "app.db"


def test_user_dir_rejects_traversal_user_id(tmp_path):
    with pytest.raises(ValueError, match="Invalid user_id"):
        DataPaths(data_root=str(tmp_path / "data"), user_id="../../escaped")

    assert not (tmp_path / "escaped").exists()


def test_user_dir_accepts_normal_user_ids(tmp_path):
    for user_id in ("default_user", "alice_test"):
        paths = DataPaths(data_root=str(tmp_path / "data"), user_id=user_id)

        user_dir = paths.user_dir

        expected = tmp_path / "data"
        if user_id != "default_user":
            expected = expected / "Users" / user_id
        assert user_dir == expected
        assert user_dir.exists()


def test_user_scoped_dirs_are_distinct_per_user(tmp_path):
    alice = DataPaths(data_root=str(tmp_path / "data"), user_id="alice_test")
    bob = DataPaths(data_root=str(tmp_path / "data"), user_id="bob_test")

    assert alice.files_dir() != bob.files_dir()
    assert alice.user_skills_dir() != bob.user_skills_dir()
    assert alice.user_subagents_dir() != bob.user_subagents_dir()
    assert alice.conversation_dir() != bob.conversation_dir()

    assert alice.files_dir() == tmp_path / "data" / "Users" / "alice_test" / "Files"
    assert bob.files_dir() == tmp_path / "data" / "Users" / "bob_test" / "Files"


def test_named_user_settings_paths_are_user_scoped(tmp_path):
    data_root = tmp_path / "data"
    alice = DataPaths(data_root=str(data_root), user_id="alice")
    bob = DataPaths(data_root=str(data_root), user_id="bob")

    assert alice.user_settings_path() == data_root / "Users" / "alice" / "settings.json"
    assert alice.user_grader_prompt_path() == data_root / "Users" / "alice" / "grader_prompt.md"
    assert alice.user_settings_path() != bob.user_settings_path()
    assert alice.user_grader_prompt_path() != bob.user_grader_prompt_path()


def test_default_user_settings_paths_are_under_data_root(tmp_path):
    data_root = tmp_path / "data"
    paths = DataPaths(data_root=str(data_root))

    assert paths.user_settings_path() == data_root / "settings.json"
    assert paths.user_grader_prompt_path() == data_root / "grader_prompt.md"


def test_user_settings_path_helpers_do_not_create_files(tmp_path):
    paths = DataPaths(data_root=str(tmp_path / "data"), user_id="alice")

    settings_path = paths.user_settings_path()
    grader_prompt_path = paths.user_grader_prompt_path()

    assert paths.user_dir.exists()
    assert not settings_path.exists()
    assert not grader_prompt_path.exists()


def test_workspace_tools_dir_is_deployment_shared(monkeypatch, tmp_path):
    """Deployment-shared Tools dir: visible to every user, outside Users/.

    get_custom_tools merges shared-then-per-user (user overrides same name),
    so this dir is the deployment-wide toolset while per-user Tools/ stays
    for user-specific tools (Jen trusted-team mode).
    """
    # Suite-ordering hygiene: the settings singleton may hold a data_root
    # from an earlier api test's patched home (combined-run flake). Force the
    # data root explicitly via env (env beats yaml) and reset caches.
    from src.config import settings as settings_module
    from src.storage.paths import _paths_cache

    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path / "Assistant"))
    _paths_cache.clear()
    settings_module._config = None

    shared = DataPaths(user_id="alice").workspace_tools_dir()
    assert shared == tmp_path / "Assistant" / "Tools"

    # Per-user tools dir is separate and wins on name collision (merge logic
    # lives in tools_custom.get_custom_tools).
    per_user = DataPaths(user_id="bob").user_tools_dir()
    assert per_user == tmp_path / "Assistant" / "Users" / "bob" / "Tools"
    assert per_user != shared
