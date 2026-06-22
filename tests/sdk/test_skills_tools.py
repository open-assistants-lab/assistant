"""Behavioral tests for SDK-native skill tools (skills_load, skills_reload)."""

from __future__ import annotations

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


def test_skills_load_returns_skill_content(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    _write_skill(user_dir, "my-helper", "My helper", "This is the skill body")
    registry = SkillRegistry(skills_dir=user_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    result = skills_tools.skills_load.invoke(
        {"name": "my-helper", "user_id": "test"}
    )

    assert "This is the skill body" in result
    assert "my-helper" in result


def test_skills_load_rejects_path_traversal_names(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    base_dir = tmp_path / "base"
    user_dir = base_dir / "user-skills"
    _write_skill(tmp_path, "whatever", "Outside skill", "outside secret")
    registry = SkillRegistry(skills_dir=user_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    result = skills_tools.skills_load.invoke(
        {"name": "../../whatever", "user_id": "test"}
    )

    assert "outside secret" not in result
    assert "not found" in result.lower()


def test_skills_load_not_found_returns_error(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    registry = SkillRegistry(skills_dir=user_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    result = skills_tools.skills_load.invoke(
        {"name": "nonexistent", "user_id": "test"}
    )

    assert "not found" in result.lower()


def test_skills_reload_refreshes_after_adding_skill(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    registry = SkillRegistry(skills_dir=user_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    # No skills initially
    reload_result = skills_tools.skills_reload.invoke(
        {"user_id": "test"}
    )
    assert "No skills available" in reload_result or "Skills reloaded" in reload_result

    # Add a skill
    _write_skill(user_dir, "new-skill", "New skill", "New body")

    # Reload should now find it
    reload_result = skills_tools.skills_reload.invoke(
        {"user_id": "test"}
    )
    assert "new-skill" in reload_result


def test_skills_reload_includes_loaded_status(tmp_path, monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    user_dir = tmp_path / "user-skills"
    _write_skill(user_dir, "my-helper", "My helper", "Body")
    registry = SkillRegistry(skills_dir=user_dir)

    monkeypatch.setattr(skills_tools, "get_skill_registry", lambda **kwargs: registry)

    # Load the skill
    skills_tools.skills_load.invoke(
        {"name": "my-helper", "user_id": "test"}
    )

    # Reload should show [loaded]
    reload_result = skills_tools.skills_reload.invoke(
        {"user_id": "test"}
    )
    assert "my-helper" in reload_result
    assert "loaded" in reload_result.lower()


def test_skills_load_calls_registry_with_user_id(monkeypatch):
    from src.sdk.tools_core import skills as skills_tools

    calls = []

    class FakeRegistry:
        def get_skill(self, name):
            return {"name": name, "content": "Test content", "metadata": {}}

        def get_all_skills(self):
            return [{"name": "my-skill", "description": "Test"}]

        def mark_skill_loaded(self, name):
            pass

        _loaded_skills = {}

    def fake_get_skill_registry(**kwargs):
        calls.append(kwargs)
        return FakeRegistry()

    monkeypatch.setattr(skills_tools, "get_skill_registry", fake_get_skill_registry)

    skills_tools.skills_load.invoke({"name": "my-skill", "user_id": "test"})

    assert calls == [{"user_id": "test"}]
