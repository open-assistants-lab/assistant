"""Tests for Pi-style skill validation.

Covers:
- warnings-not-errors: invalid names fall back to the directory name, long
  descriptions still load, only missing descriptions skip
- ignore files (.gitignore / .ignore / .fdignore) respected during discovery
- symlink dedup and name-collision diagnostics
- disable-model-invocation excluded from the catalog
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from src.skills.registry import SkillRegistry
from src.skills.storage import SkillStorage


def _write_skill(base: Path, name: str, frontmatter: str, body: str = "Content") -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
    return p


def _registry(base: Path) -> SkillRegistry:
    # Prevent bundled seed skills from being copied into the temp dir
    (base / ".skills_seeded").write_text("", encoding="utf-8")
    return SkillRegistry(skills_dir=base)


# ---------------------------------------------------------------------------
# Warnings-not-errors
# ---------------------------------------------------------------------------


def test_invalid_name_falls_back_to_directory_name():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        _write_skill(base, "my_skill", "name: Bad Name!\ndescription: A skill\n")
        registry = _registry(base)

        skills = registry.get_all_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "my_skill"

        diagnostics = registry.get_diagnostics()
        assert any(diag["type"] == "warning" for diag in diagnostics)


def test_missing_description_skipped_with_diagnostic():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        _write_skill(base, "no-desc", "name: no-desc\n")
        registry = _registry(base)

        assert registry.get_all_skills() == []
        diagnostics = registry.get_diagnostics()
        assert any(
            diag["type"] == "warning" and "description" in diag["message"]
            for diag in diagnostics
        )


def test_long_description_loads_with_warning():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        _write_skill(base, "long-desc", f"name: long-desc\ndescription: {'x' * 2000}\n")
        registry = _registry(base)

        skills = registry.get_all_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "long-desc"
        assert any(diag["type"] == "warning" for diag in registry.get_diagnostics())


def test_valid_skill_has_no_diagnostics():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        _write_skill(base, "good-skill", "name: good-skill\ndescription: A fine skill\n")
        registry = _registry(base)

        assert len(registry.get_all_skills()) == 1
        assert registry.get_diagnostics() == []


# ---------------------------------------------------------------------------
# Ignore files
# ---------------------------------------------------------------------------


def test_gitignore_respected():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        _write_skill(base, "kept-skill", "name: kept-skill\ndescription: Kept\n")
        _write_skill(base, "ignored-skill", "name: ignored-skill\ndescription: Ignored\n")
        (base / ".gitignore").write_text("ignored-skill/\n", encoding="utf-8")
        registry = _registry(base)

        names = [s["name"] for s in registry.get_all_skills()]
        assert names == ["kept-skill"]


def test_ignore_negation():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        _write_skill(base, "keep-skill", "name: keep-skill\ndescription: Kept\n")
        _write_skill(base, "drop-skill", "name: drop-skill\ndescription: Dropped\n")
        (base / ".ignore").write_text("*\n!keep-skill/\n", encoding="utf-8")
        registry = _registry(base)

        names = [s["name"] for s in registry.get_all_skills()]
        assert names == ["keep-skill"]


def test_fdignore_respected():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        _write_skill(base, "kept-skill", "name: kept-skill\ndescription: Kept\n")
        _write_skill(base, "hidden-skill", "name: hidden-skill\ndescription: Hidden\n")
        (base / ".fdignore").write_text("hidden-skill\n", encoding="utf-8")
        registry = _registry(base)

        names = [s["name"] for s in registry.get_all_skills()]
        assert names == ["kept-skill"]


# ---------------------------------------------------------------------------
# Symlink dedup + collisions
# ---------------------------------------------------------------------------


def test_symlink_dedup():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        _write_skill(base, "alpha", "name: alpha\ndescription: Real skill\n")
        os.symlink(base / "alpha", base / "alpha-link")
        registry = _registry(base)

        skills = registry.get_all_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "alpha"
        assert not any(diag["type"] == "collision" for diag in registry.get_diagnostics())


def test_name_collision_diagnostic():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        _write_skill(base, "one", "name: shared\ndescription: First\n")
        _write_skill(base, "two", "name: shared\ndescription: Second\n")
        registry = _registry(base)

        skills = registry.get_all_skills()
        assert len(skills) == 1
        assert skills[0]["description"] == "First"  # first wins

        collisions = [diag for diag in registry.get_diagnostics() if diag["type"] == "collision"]
        assert len(collisions) == 1
        assert collisions[0]["collision"]["name"] == "shared"
        assert collisions[0]["collision"]["winnerPath"].endswith("one")
        assert collisions[0]["collision"]["loserPath"].endswith("two")


# ---------------------------------------------------------------------------
# disable-model-invocation
# ---------------------------------------------------------------------------


def test_disable_model_invocation_excluded_from_catalog():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        _write_skill(
            base,
            "manual-only",
            "name: manual-only\ndescription: Manual only\nmetadata:\n  disable_model_invocation: true\n",
        )
        _write_skill(base, "auto-skill", "name: auto-skill\ndescription: Auto\n")
        registry = _registry(base)

        # Still discoverable via get_all_skills
        names = [s["name"] for s in registry.get_all_skills()]
        assert "manual-only" in names

        # Excluded from the system-prompt catalog
        descriptions = registry.get_skill_descriptions()
        assert not any("manual-only" in d for d in descriptions)
        assert any("auto-skill" in d for d in descriptions)


# ---------------------------------------------------------------------------
# Storage-level diagnostics
# ---------------------------------------------------------------------------


def test_storage_load_skills_with_diagnostics():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        _write_skill(base, "good", "name: good\ndescription: Fine\n")
        _write_skill(base, "bad-name", "name: Not Valid!\ndescription: Has desc\n")
        storage = SkillStorage(base)

        skills, diagnostics = storage.load_skills_with_diagnostics()
        names = [s["name"] for s in skills]
        assert "good" in names
        assert "bad-name" in names  # falls back to dir name, still loads
        assert any(diag["type"] == "warning" for diag in diagnostics)
