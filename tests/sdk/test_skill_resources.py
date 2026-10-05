"""Tests for skills_load basic behavior."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from src.sdk.tools_core.skills import skills_load


class TestSkillResourceEnumeration:
    def test_skills_load_returns_skill_content(self):
        """skills_load() returns the skill content wrapped in tags."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "my-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: my-skill\ndescription: Test\n---\n\nDo the thing."
            )

            with patch("src.sdk.tools_core.skills.get_skill_registry") as mock_reg:
                mock_reg.return_value.get_skill.return_value = {
                    "name": "my-skill",
                    "content": "Do the thing.",
                    "path": str(skill_dir),
                    "metadata": {"scope": "user"},
                }
                mock_reg.return_value._loaded_skills = {}
                mock_reg.return_value.mark_skill_loaded = lambda n: None

                result = skills_load.invoke({
                    "name": "my-skill",
                    "user_id": "test_user",
                    "workspace_id": "personal",
                })

                assert "Do the thing." in result
                # The envelope carries the skill's own resource directory (#100) so
        # relative scripts/references in a skill resolve.
        assert '<skill_content name="my-skill"' in result
        assert "base_dir=" in result

    def test_skills_load_not_found_returns_error(self):
        """skills_load() returns an error for non-existent skills."""
        with tempfile.TemporaryDirectory():
            with patch("src.sdk.tools_core.skills.get_skill_registry") as mock_reg:
                mock_reg.return_value.get_skill.return_value = None
                mock_reg.return_value.get_all_skills.return_value = []
                mock_reg.return_value._loaded_skills = {}

                result = skills_load.invoke({
                    "name": "nonexistent",
                    "user_id": "test_user",
                    "workspace_id": "personal",
                })

                assert "not found" in result.lower()

    def test_skills_load_with_empty_content(self):
        """Skill with no content returns appropriate message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "empty-skill"
            skill_dir.mkdir()

            with patch("src.sdk.tools_core.skills.get_skill_registry") as mock_reg:
                mock_reg.return_value.get_skill.return_value = {
                    "name": "empty-skill",
                    "content": "",
                    "path": str(skill_dir),
                    "metadata": {"scope": "user"},
                }
                mock_reg.return_value._loaded_skills = {}
                mock_reg.return_value.mark_skill_loaded = lambda n: None

                result = skills_load.invoke({
                    "name": "empty-skill",
                    "user_id": "test_user",
                    "workspace_id": "personal",
                })

                assert "exists but has no content" in result
