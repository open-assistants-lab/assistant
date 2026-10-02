"""Bulk-store ownership must outrank filesystem project grants (#56)."""

from types import SimpleNamespace

import pytest

from src.sdk.tools_core import filesystem as fs
from src.storage.paths import DEFAULT_USER_ID, DataPaths


@pytest.fixture
def tenant_paths(tmp_path, monkeypatch):
    root = tmp_path / "store"
    settings = SimpleNamespace(filesystem=SimpleNamespace(allowed_roots=[], workspace_root=None))
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        fs, "get_paths",
        lambda user, workspace_id="personal": DataPaths(
            user_id=user, data_root=root, data_path=tmp_path / "config", workspace_id=workspace_id
        ),
    )
    token = fs._current_user_id.set(DEFAULT_USER_ID)
    yield root, settings
    fs._current_user_id.reset(token)


@pytest.mark.parametrize("user", ["alice", DEFAULT_USER_ID])
@pytest.mark.parametrize("directory", ["Files", "Skills", "Tools", "Subagents", "Memory"])
@pytest.mark.parametrize("via_link", [False, True])
def test_sibling_user_path_rejected(tenant_paths, user, directory, via_link):
    root, settings = tenant_paths
    target = root / "Users" / "bob" / directory / "private.txt"
    target.parent.mkdir(parents=True)
    target.write_text("bob secret")
    own_files = fs.get_paths(user).workspace_files_dir()
    path = target
    if via_link:
        path = own_files / "link"
        path.symlink_to(target)
    # Even a broad operator project grant must not disable store ownership.
    settings.filesystem.allowed_roots = [str(root.parent)]
    with pytest.raises(ValueError):
        fs._resolve_path(str(path), user)


@pytest.mark.parametrize("user", ["alice", DEFAULT_USER_ID])
def test_own_paths_and_external_project_remain_available(tenant_paths, user):
    root, settings = tenant_paths
    paths = fs.get_paths(user)
    for folder in [paths.workspace_files_dir(), paths.user_skills_dir(), paths.user_tools_dir(),
                   paths.user_subagents_dir(), paths.user_memory_dir()]:
        target = folder / "own.txt"
        assert fs._resolve_path(str(target), user) == target.resolve()
    project = root.parent / "project"
    project.mkdir()
    settings.filesystem.allowed_roots = [str(project)]
    assert fs._resolve_path(str(project / "code.py"), user) == project / "code.py"


def test_named_user_cannot_read_default_user_store(tenant_paths):
    root, settings = tenant_paths
    settings.filesystem.allowed_roots = [str(root)]
    with pytest.raises(ValueError):
        fs._resolve_path(str(root / "Files" / "private.txt"), "alice")


def test_relative_legacy_root_cannot_bypass_ownership(tenant_paths):
    root, settings = tenant_paths
    settings.filesystem.workspace_root = str(root / "Users" / "bob" / "Files")
    with pytest.raises(ValueError):
        fs._resolve_path("private.txt", "alice")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["files_read", "files_write"])
async def test_tool_cannot_access_sibling_bytes(tenant_paths, operation):
    root, _ = tenant_paths
    target = root / "Users" / "bob" / "Files" / "private.txt"
    target.parent.mkdir(parents=True)
    target.write_text("bob secret")
    args = {"path": str(target), "user_id": "alice"}
    if operation == "files_write":
        args["content"] = "changed"
    result = await getattr(fs, operation).ainvoke(args)
    assert "bob secret" not in result.content
    assert result.is_error
    assert target.read_text() == "bob secret"
