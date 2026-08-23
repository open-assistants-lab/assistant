"""Tests for seed-skill refresh and web-automation skill discovery.

Covers:
- seed skills are re-copied when the seed content changes AND the user's
  copy is untouched (sidecar hash tracking)
- user-modified copies are never overwritten
- the web-automation skill (browser-tools migration) is discoverable and
  contains the agent-browser CLI stub
"""

from __future__ import annotations

from pathlib import Path

from src.skills.registry import SkillRegistry


def _write_seed_skill(base: Path, name: str, body: str) -> None:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")


def _skill_body(name: str, description: str, content: str = "Content") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n{content}"


def _registry(skills_dir: Path) -> SkillRegistry:
    return SkillRegistry(skills_dir=skills_dir)


# ---------------------------------------------------------------------------
# Seed refresh
# ---------------------------------------------------------------------------


def test_untouched_stale_copy_is_refreshed(monkeypatch, tmp_path):
    seed_dir = tmp_path / "seeds" / "skills"
    user_dir = tmp_path / "user"
    _write_seed_skill(seed_dir, "demo", _skill_body("demo", "v1"))
    monkeypatch.chdir(tmp_path)

    # First run: seed copied
    reg = _registry(user_dir)
    reg._seed_system_skills()
    assert "description: v1" in (user_dir / "demo" / "SKILL.md").read_text()

    # Seed changes
    _write_seed_skill(seed_dir, "demo", _skill_body("demo", "v2"))

    # Second run: untouched copy refreshed
    reg2 = _registry(user_dir)
    reg2._seed_system_skills()
    assert "description: v2" in (user_dir / "demo" / "SKILL.md").read_text()


def test_user_modified_copy_is_never_overwritten(monkeypatch, tmp_path):
    seed_dir = tmp_path / "seeds" / "skills"
    user_dir = tmp_path / "user"
    _write_seed_skill(seed_dir, "demo", _skill_body("demo", "v1"))
    monkeypatch.chdir(tmp_path)

    reg = _registry(user_dir)
    reg._seed_system_skills()

    # User edits their copy
    user_file = user_dir / "demo" / "SKILL.md"
    user_file.write_text("---\nname: demo\ndescription: user edit\n---\nMINE", encoding="utf-8")

    # Seed changes
    _write_seed_skill(seed_dir, "demo", _skill_body("demo", "v2"))

    reg2 = _registry(user_dir)
    reg2._seed_system_skills()
    assert "MINE" in user_file.read_text()
    assert "v2" not in user_file.read_text()


def test_unchanged_seed_does_not_touch_copy(monkeypatch, tmp_path):
    seed_dir = tmp_path / "seeds" / "skills"
    user_dir = tmp_path / "user"
    _write_seed_skill(seed_dir, "demo", _skill_body("demo", "v1"))
    monkeypatch.chdir(tmp_path)

    reg = _registry(user_dir)
    reg._seed_system_skills()
    user_file = user_dir / "demo" / "SKILL.md"
    mtime = user_file.stat().st_mtime_ns

    reg2 = _registry(user_dir)
    reg2._seed_system_skills()
    assert user_file.stat().st_mtime_ns == mtime


# ---------------------------------------------------------------------------
# web-automation skill (browser-tools migration)
# ---------------------------------------------------------------------------


def test_web_automation_skill_is_discoverable(tmp_path):
    # No chdir: reads the real seeds/skills from the repo root
    user_dir = tmp_path / "user"
    reg = _registry(user_dir)

    skills = reg.get_all_skills()
    wa = next((s for s in skills if s["name"] == "web-automation"), None)
    assert wa is not None
    assert "agent-browser skills get core" in wa["content"]
    assert "agent-browser get title" in wa["content"]
    assert "browser_open" in wa["content"]
    assert "electron" not in wa["description"].lower()


def test_web_automation_in_catalog_descriptions(tmp_path):
    # No chdir: reads the real seeds/skills from the repo root
    user_dir = tmp_path / "user"
    reg = _registry(user_dir)

    descriptions = reg.get_skill_descriptions()
    assert any("web-automation" in d for d in descriptions)
