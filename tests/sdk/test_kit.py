"""Tests for kit factory (P1-T7): install/validate/list/uninstall round-trip.

Kit = content: PROFILE.md (agent template) + skills/ + rubrics/ + eval set.
Third vertical must be authorable with zero src/ changes (factory exit).
"""

from pathlib import Path

import pytest

from src.sdk.kit import kit_install, kit_list, kit_uninstall, kit_validate

SKILL_MD = """---
name: {name}
description: {desc}
---

# {title}

Methodology body.
"""

PROFILE_MD = """---
name: {name}
description: {desc} template agent
model: "ollama:test-model"
tools: []
---

You are the {title} assistant.
"""

PERSONAS_YAML = """personas:
  - id: kitp1
    name: Kit Persona One
    style: detailed
    description: Tests detailed interactions
    sample_phrases:
      - draft a campaign plan
      - summarize this quarter
"""


def _make_kit(root: Path, name: str = "test-vertical") -> Path:
    kit_dir = root / "kits" / name
    (kit_dir / "skills" / "demo-skill").mkdir(parents=True)
    (kit_dir / "rubrics").mkdir(parents=True)
    (kit_dir / "eval").mkdir(parents=True)
    (kit_dir / "kit.yaml").write_text(
        f"name: {name}\ndescription: Test vertical kit\nversion: 0.1.0\n"
    )
    (kit_dir / "PROFILE.md").write_text(
        PROFILE_MD.format(name=name, desc="Kit", title="Test Vertical")
    )
    (kit_dir / "skills" / "demo-skill" / "SKILL.md").write_text(
        SKILL_MD.format(name="demo-skill", desc="Demo skill", title="Demo Skill")
    )
    (kit_dir / "rubrics" / "rubric.md").write_text("# Rubric\n- helpful\n- accurate\n")
    (kit_dir / "eval" / "personas.yaml").write_text(PERSONAS_YAML)
    return kit_dir


@pytest.fixture
def kit_env(tmp_path, monkeypatch):
    """Isolated data root + kit dir; skills/subagents land under tmp data root."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    kit = _make_kit(tmp_path)
    # subagents + skills dirs resolve through DataPaths(data_root=home-ish);
    # point data_root at tmp for isolation.
    import src.config.settings as settings_mod

    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path / "assistant_data"))
    monkeypatch.setattr(settings_mod, "_config", None)
    try:
        yield kit, tmp_path
    finally:
        monkeypatch.setattr(settings_mod, "_config", None)


class TestKitValidate:
    def test_valid_kit_passes(self, kit_env):
        kit, _ = kit_env
        assert kit_validate(kit) == []

    def test_rejects_missing_profile(self, kit_env):
        kit, _ = kit_env
        (kit / "PROFILE.md").unlink()
        problems = kit_validate(kit)
        assert any("PROFILE.md" in p for p in problems)

    def test_rejects_bad_skill_frontmatter(self, kit_env):
        kit, _ = kit_env
        (kit / "skills" / "demo-skill" / "SKILL.md").write_text("no frontmatter")
        problems = kit_validate(kit)
        assert any("demo-skill" in p for p in problems)

    def test_rejects_missing_rubric(self, kit_env):
        kit, _ = kit_env
        (kit / "rubrics" / "rubric.md").unlink()
        problems = kit_validate(kit)
        assert any("rubric" in p.lower() for p in problems)

    def test_rejects_bad_personas_yaml(self, kit_env):
        kit, _ = kit_env
        (kit / "eval" / "personas.yaml").write_text("personas: [unclosed")
        problems = kit_validate(kit)
        assert any("personas" in p for p in problems)


class TestKitInstallRoundTrip:
    def test_install_writes_profile_skills_rubric(self, kit_env):
        kit, root = kit_env
        summary = kit_install(kit)
        assert summary["installed_skills"] == ["demo-skill"]
        assert summary["profile_path"].exists()
        rubrics = list(summary["rubric_dir"].glob("*.md"))
        assert len(rubrics) == 1

    def test_install_uninstall_leaves_no_orphans(self, kit_env):
        kit, _ = kit_env
        kit_install(kit)
        kit_uninstall("test-vertical")
        # subagent dir + skill copies gone
        from src.storage.paths import DataPaths

        paths = DataPaths(user_id="default_user")
        assert not (paths.user_subagents_dir() / "test-vertical" / "PROFILE.md").exists()
        assert not (paths.user_skills_dir() / "demo-skill").exists()
        # drafts dir has no residue either
        drafts = paths.user_skills_dir().parent / ".skill-drafts"
        if drafts.exists():
            assert not any(d.name == "demo-skill" for d in drafts.iterdir())

    def test_idempotent_reinstall(self, kit_env):
        kit, _ = kit_env
        kit_install(kit)
        summary = kit_install(kit)  # replace, not duplicate
        assert summary["installed_skills"] == ["demo-skill"]


class TestKitList:
    def test_lists_valid_and_invalid(self, kit_env, tmp_path):
        kit, root = kit_env
        bad = root / "kits" / "broken-kit"
        bad.mkdir(parents=True)
        entries = kit_list(kits_root=root / "kits")
        by_name = {e["name"]: e for e in entries}
        assert by_name["test-vertical"]["valid"] is True
        assert by_name["broken-kit"]["valid"] is False
