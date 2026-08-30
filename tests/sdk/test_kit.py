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


def _make_kit(
    root: Path, name: str = "test-vertical", tools: list[str] | None = None
) -> Path:
    kit_dir = root / "kits" / name
    (kit_dir / "skills" / "demo-skill").mkdir(parents=True)
    (kit_dir / "rubrics").mkdir(parents=True)
    (kit_dir / "eval").mkdir(parents=True)
    manifest = f"name: {name}\ndescription: Test vertical kit\nversion: 0.1.0\n"
    if tools is not None:
        manifest += "tools:\n  enable:\n" + "".join(f"    - {tool}\n" for tool in tools)
    (kit_dir / "kit.yaml").write_text(manifest)
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
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path / "data"))
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

    def test_rejects_unknown_manifest_tool(self, kit_env):
        kit, _ = kit_env
        with (kit / "kit.yaml").open("a", encoding="utf-8") as manifest:
            manifest.write("tools:\n  enable:\n    - professional_magic\n")

        problems = kit_validate(kit)

        assert any("unknown tool" in problem and "professional_magic" in problem for problem in problems)


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

    def test_install_enables_manifest_tools_and_uninstall_restores_exact_scopes(self, kit_env):
        kit, _ = kit_env
        with (kit / "kit.yaml").open("a", encoding="utf-8") as manifest:
            manifest.write("tools:\n  enable:\n    - interview_start\n    - app_summarize\n")

        from src.sdk.capabilities import (
            load_user_capabilities,
            resource_enabled,
            set_resource_enabled,
        )

        set_resource_enabled("default_user", "tools", "interview_start", False)
        assert "app_summarize" not in load_user_capabilities("default_user")["tools"]

        kit_install(kit)
        installed = load_user_capabilities("default_user")
        assert resource_enabled(installed, "tools", "interview_start") is True
        assert resource_enabled(installed, "tools", "app_summarize") is True

        kit_uninstall("test-vertical")
        restored = load_user_capabilities("default_user")
        assert restored["tools"]["interview_start"] is False
        assert "app_summarize" not in restored["tools"]
        assert resource_enabled(restored, "tools", "app_summarize") is False


class TestKitList:
    def test_lists_valid_and_invalid(self, kit_env, tmp_path):
        kit, root = kit_env
        bad = root / "kits" / "broken-kit"
        bad.mkdir(parents=True)
        entries = kit_list(kits_root=root / "kits")
        by_name = {e["name"]: e for e in entries}
        assert by_name["test-vertical"]["valid"] is True
        assert by_name["broken-kit"]["valid"] is False


class TestReinstallSafety:
    def test_failed_reinstall_restores_modified_live_skill(self, kit_env):
        """Rollback restores the user's hand-edited live skill bit-for-bit.

        Review P1: the previous install rmtree'd the live copy before the new
        content landed; a failure after that point (e.g. eval copy) both
        dropped the new install AND rolled back into nothing — the user's
        customized skill was unrecoverable.
        """
        from unittest.mock import patch

        from src.skills.registry import SkillRegistry

        kit, tmp_path = kit_env
        kit_install(kit)
        # user customizes the live copy after install
        reg = SkillRegistry(user_id="default_user", workspace_id="personal")
        live = reg.skills_dir / "demo-skill" / "SKILL.md"
        live.write_text(
            live.read_text(encoding="utf-8") + "\n# user customization\n",
            encoding="utf-8",
        )
        original = live.read_text(encoding="utf-8")

        # inject a mid-install failure: rubric copy blows up after the skill
        # has been approved
        real_copy = __import__("shutil").copyfile

        def failing_copy(src, dst, *a, **k):  # type: ignore[no-untyped-def]
            if str(dst).endswith("rubric.md"):
                raise OSError("injected mid-install failure")
            return real_copy(src, dst, *a, **k)

        with patch("shutil.copyfile", side_effect=failing_copy):
            with pytest.raises(OSError, match="injected"):
                kit_install(kit)

        restored = reg.skills_dir / "demo-skill" / "SKILL.md"
        assert restored.read_text(encoding="utf-8") == original  # bit-for-bit

    def test_successful_reinstall_cleans_up_backup(self, kit_env):
        import glob
        import os

        kit, _ = kit_env
        kit_install(kit)
        kit_install(kit)  # success path
        leftovers = glob.glob(os.path.join(os.path.sep, "tmp", "kit-install-backup-*"))
        assert leftovers == []


class TestFactoryRoundTrip:
    def test_factory_stamps_valid_kit(self, tmp_path):
        """kits/factory.py stamps a kit from _template that passes kit_validate.

        Review P1: the empty _template made a fresh clone unable to reproduce
        a third vertical — the factory exit criterion must be demonstrable
        from the committed state alone.
        """
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable, "kits/factory.py",
                "--name", "factory-rt",
                "--description", "round-trip methodology",
                "--persona", "factory round-trip persona",
                "--kits-root", str(tmp_path),
            ],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        stamped = tmp_path / "factory-rt"
        assert (stamped / "skills" / "example-skill" / "SKILL.md").is_file()
        assert kit_validate(stamped) == []
