"""Behavioral tests for SDK-native skill tools (skills_load, skills_reload)."""

from __future__ import annotations

import builtins
from pathlib import Path

from src.skills.registry import SkillRegistry


def _write_skill(root: Path, name: str, description: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
description: {description}
---
{body}
""",
        encoding="utf-8",
    )


class _FakePaths:
    def __init__(self, user_skills: Path, workspace_skills: Path):
        self._user_skills = user_skills
        self._workspace_skills = workspace_skills

    def user_skills_dir(self) -> Path:
        self._user_skills.mkdir(parents=True, exist_ok=True)
        return self._user_skills

    def skills_dir(self) -> Path:
        return self.user_skills_dir()

    def workspace_skills_dir(self) -> Path:
        self._workspace_skills.mkdir(parents=True, exist_ok=True)
        return self._workspace_skills

    @property
    def root(self) -> Path:
        return self._user_skills.parent


def test_skills_load_returns_skill_content(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    workspace_dir = tmp_path / "workspace-skills"
    _write_skill(user_dir, "my-helper", "My helper", "This is the skill body")
    registry = SkillRegistry(skills_dir=user_dir, workspace_skills_dir=workspace_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    result = skills_tools.skills_load.invoke(
        {"name": "my-helper", "user_id": "test", "workspace_id": "ws1"}
    )

    assert "This is the skill body" in result
    assert "my-helper" in result


def test_skills_load_ignores_workspace_skill(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    workspace_dir = tmp_path / "workspace-skills"
    _write_skill(user_dir, "shared", "User shared", "User version")
    _write_skill(workspace_dir, "shared", "Workspace shared", "Workspace version")
    registry = SkillRegistry(skills_dir=user_dir, workspace_skills_dir=workspace_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    result = skills_tools.skills_load.invoke(
        {"name": "shared", "user_id": "test", "workspace_id": "ws1"}
    )

    assert "User version" in result
    assert "Workspace version" not in result


def test_skills_load_rejects_path_traversal_names(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    base_dir = tmp_path / "base"
    user_dir = base_dir / "user-skills"
    workspace_dir = base_dir / "workspace-skills"
    _write_skill(tmp_path, "whatever", "Outside skill", "outside secret")
    registry = SkillRegistry(skills_dir=user_dir, workspace_skills_dir=workspace_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    result = skills_tools.skills_load.invoke(
        {"name": "../../whatever", "user_id": "test", "workspace_id": "ws1"}
    )

    assert "outside secret" not in result
    assert "not found" in result.lower()


def test_skills_load_not_found_returns_error(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    workspace_dir = tmp_path / "workspace-skills"
    registry = SkillRegistry(skills_dir=user_dir, workspace_skills_dir=workspace_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    result = skills_tools.skills_load.invoke(
        {"name": "nonexistent", "user_id": "test", "workspace_id": "ws1"}
    )

    assert "not found" in result.lower()


def test_skills_reload_refreshes_after_adding_skill(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    workspace_dir = tmp_path / "workspace-skills"
    registry = SkillRegistry(skills_dir=user_dir, workspace_skills_dir=workspace_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    # No skills initially
    reload_result = skills_tools.skills_reload.invoke(
        {"user_id": "test", "workspace_id": "ws1"}
    )
    assert "No skills available" in reload_result or "Skills reloaded" in reload_result

    # Add a skill
    _write_skill(user_dir, "new-skill", "New skill", "New body")

    # Reload should now find it
    reload_result = skills_tools.skills_reload.invoke(
        {"user_id": "test", "workspace_id": "ws1"}
    )
    assert "new-skill" in reload_result


def test_skills_reload_includes_loaded_status(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    workspace_dir = tmp_path / "workspace-skills"
    _write_skill(user_dir, "my-helper", "My helper", "Body")
    registry = SkillRegistry(skills_dir=user_dir, workspace_skills_dir=workspace_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    # Load the skill
    skills_tools.skills_load.invoke(
        {"name": "my-helper", "user_id": "test", "workspace_id": "ws1"}
    )

    # Reload should show [loaded]
    reload_result = skills_tools.skills_reload.invoke(
        {"user_id": "test", "workspace_id": "ws1"}
    )
    assert "my-helper" in reload_result
    assert "loaded" in reload_result.lower()


def test_skills_load_is_user_catalog_level(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    workspace_dir = tmp_path / "workspace-skills"
    _write_skill(user_dir, "my-helper", "My helper", "Body")
    registry = SkillRegistry(skills_dir=user_dir, workspace_skills_dir=workspace_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    result = skills_tools.skills_load.invoke(
        {"name": "my-helper", "user_id": "test", "workspace_id": "ws1"}
    )

    assert "Body" in result


def test_skills_reload_is_user_catalog_level(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    workspace_dir = tmp_path / "workspace-skills"
    _write_skill(user_dir, "my-helper", "My helper", "Body")
    registry = SkillRegistry(skills_dir=user_dir, workspace_skills_dir=workspace_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    result = skills_tools.skills_reload.invoke({"user_id": "test", "workspace_id": "ws1"})

    assert "my-helper" in result


def test_skills_load_rejects_user_disabled_skill(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    workspace_dir = tmp_path / "workspace-skills"
    _write_skill(user_dir, "disabled-helper", "Disabled helper", "Secret body")
    (tmp_path / "capabilities.yaml").write_text(
        "skills:\n  disabled-helper: false\n",
        encoding="utf-8",
    )
    registry = SkillRegistry(skills_dir=user_dir, workspace_skills_dir=workspace_dir)

    monkeypatch.setattr("src.sdk.capabilities.user_capabilities_root", lambda user_id: tmp_path)
    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)
    monkeypatch.setattr(
        skills_tools,
        "get_paths",
        lambda *args, **kwargs: _FakePaths(user_dir, workspace_dir),
        raising=False,
    )

    result = skills_tools.skills_load.invoke(
        {"name": "disabled-helper", "user_id": "test", "workspace_id": "ws1"}
    )

    assert "disabled" in result.lower()
    assert "Secret body" not in result


def test_skills_reload_omits_user_disabled_skills(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    workspace_dir = tmp_path / "workspace-skills"
    _write_skill(user_dir, "enabled-helper", "Enabled helper", "Enabled body")
    _write_skill(user_dir, "disabled-helper", "Disabled helper", "Disabled body")
    (tmp_path / "capabilities.yaml").write_text(
        "skills:\n  disabled-helper: false\n",
        encoding="utf-8",
    )
    registry = SkillRegistry(skills_dir=user_dir, workspace_skills_dir=workspace_dir)

    monkeypatch.setattr("src.sdk.capabilities.user_capabilities_root", lambda user_id: tmp_path)
    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)
    monkeypatch.setattr(
        skills_tools,
        "get_paths",
        lambda *args, **kwargs: _FakePaths(user_dir, workspace_dir),
        raising=False,
    )

    result = skills_tools.skills_reload.invoke({"user_id": "test", "workspace_id": "ws1"})

    assert "enabled-helper" in result
    assert "disabled-helper" not in result


class _ItemScopesImported(BaseException):
    pass


def test_skills_tools_do_not_import_item_scopes(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    workspace_dir = tmp_path / "workspace-skills"
    _write_skill(user_dir, "my-helper", "My helper", "Body")
    registry = SkillRegistry(skills_dir=user_dir, workspace_skills_dir=workspace_dir)
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "src.sdk.item_scopes":
            raise _ItemScopesImported(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)
    monkeypatch.setattr(builtins, "__import__", blocked_import)

    load_result = skills_tools.skills_load.invoke(
        {"name": "my-helper", "user_id": "test", "workspace_id": "ws1"}
    )
    reload_result = skills_tools.skills_reload.invoke({"user_id": "test", "workspace_id": "ws1"})

    assert "Body" in load_result
    assert "my-helper" in reload_result


def test_get_registry_ignores_workspace_id_for_runtime(monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    calls = []

    def fake_get_skill_registry(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(skills_tools, "get_skill_registry", fake_get_skill_registry)

    registry = skills_tools._get_registry("test", "ws1")

    assert registry is not None
    assert calls == [{"user_id": "test"}]
