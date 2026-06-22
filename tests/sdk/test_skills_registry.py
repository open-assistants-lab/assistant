"""Tests for workspace-aware skill registry."""

import tempfile
from pathlib import Path

from src.skills.registry import SkillRegistry, get_skill_registry, reset_skill_registries


def test_registry_scope_fields_populated():
    """User skills get scope=user."""
    with tempfile.TemporaryDirectory() as user_d:
        (Path(user_d) / "alpha").mkdir(parents=True)
        (Path(user_d) / "alpha" / "SKILL.md").write_text("""---
name: alpha
description: User only
---
Content A""")

        registry = SkillRegistry(skills_dir=user_d)
        skills = registry.get_all_skills()

        alpha = next(s for s in skills if s["name"] == "alpha")

        assert alpha["name"] == "alpha"
        assert alpha["description"] == "User only"
        assert "Content A" in alpha["content"]


def test_get_skill_registry_cached_by_user():
    """Factory caches per user_id."""
    reset_skill_registries()
    r1 = get_skill_registry(user_id="alice")
    r2 = get_skill_registry(user_id="alice")
    r3 = get_skill_registry(user_id="project-x")
    r4 = get_skill_registry(user_id="bob")

    assert r1 is r2
    assert r1 is not r3
    assert r1 is not r4


def test_get_skill_registry_uses_tuple_cache_keys():
    """Factory cache keys are tuples."""
    from src.skills import registry as skills_registry

    reset_skill_registries()

    r1 = get_skill_registry(user_id="a")
    r2 = get_skill_registry(user_id="a-b")

    assert r1 is not r2
    assert set(skills_registry._registries) == {("a",), ("a-b",)}


def test_seeded_skill_deleted_after_reload_is_not_reseeded(tmp_path, monkeypatch):
    """Seed marker makes seeded skills normal user skills after first seed."""
    seed_root = tmp_path / "src" / "skills_seed"
    seeded = seed_root / "seeded-skill"
    seeded.mkdir(parents=True)
    (seeded / "SKILL.md").write_text("""---
name: seeded-skill
description: Seeded Skill
---
Seeded body
""", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    user_dir = tmp_path / "user-skills"
    registry = SkillRegistry(skills_dir=user_dir)

    assert "seeded-skill" in registry.list_skills()
    assert (user_dir / ".skills_seeded").exists()

    import shutil

    shutil.rmtree(user_dir / "seeded-skill")
    registry.reload()

    assert "seeded-skill" not in registry.list_skills()
