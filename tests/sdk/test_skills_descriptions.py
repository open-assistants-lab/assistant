"""Tests for skill description budget in system prompt."""

from unittest.mock import patch

from src.sdk.runner import _get_skills_context


class TestSkillDescriptionBudget:
    SKILL_DESC_BUDGET = 1536

    def test_includes_all_skills_under_budget(self):
        """When total descriptions fit in budget, all are included."""
        skills = [
            {"name": "skill-a", "description": "Short desc A"},
            {"name": "skill-b", "description": "Short desc B"},
        ]
        with patch("src.skills.registry.get_skill_registry") as mock_reg:
            mock_reg.return_value.get_all_skills.return_value = skills
            mock_reg.return_value.get_loaded_skills.return_value = []
            mock_reg.return_value.get_load_count.return_value = 0
            result = _get_skills_context("test_user")
            assert "skill-a" in result
            assert "skill-b" in result

    def test_drops_least_loaded_when_over_budget(self):
        """When descriptions exceed budget, drop skills with lowest load count first."""
        skills = [
            {"name": "skill-a", "description": "A" * 1000},
            {"name": "skill-b", "description": "B" * 1000},
        ]
        with patch("src.skills.registry.get_skill_registry") as mock_reg:
            mock_reg.return_value.get_all_skills.return_value = skills
            mock_reg.return_value.get_loaded_skills.return_value = ["skill-a"]

            def load_count(name):
                return 5 if name == "skill-a" else 0

            mock_reg.return_value.get_load_count.side_effect = load_count
            result = _get_skills_context("test_user")
            assert "skill-a" in result
            assert "skill-b" not in result

    def test_all_dropped_returns_empty(self):
        """When no skills fit in the budget, return empty string."""
        skills = [
            {"name": "skill-a", "description": "A" * 2000},
            {"name": "skill-b", "description": "B" * 2000},
        ]
        with patch("src.skills.registry.get_skill_registry") as mock_reg:
            mock_reg.return_value.get_all_skills.return_value = skills
            mock_reg.return_value.get_loaded_skills.return_value = []
            mock_reg.return_value.get_load_count.return_value = 0
            result = _get_skills_context("test_user")
            assert result == ""
