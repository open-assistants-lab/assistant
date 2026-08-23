"""Skill storage layer for loading skills from filesystem."""

import fnmatch
from pathlib import Path
from typing import Any

from src.skills.models import Skill, _is_valid_skill_name, parse_skill_file_with_diagnostics

IGNORE_FILE_NAMES = (".gitignore", ".ignore", ".fdignore")


class _IgnoreMatcher:
    """Minimal gitignore-style matcher for skill discovery.

    Supports blank lines, comments, ``!`` negation, and leading ``/``
    anchoring. Patterns are matched against relative paths (posix form)
    with fnmatch; the last matching rule wins.
    """

    def __init__(self, base_dir: Path):
        self._rules: list[tuple[bool, str]] = []  # (negated, pattern)
        for filename in IGNORE_FILE_NAMES:
            ignore_path = base_dir / filename
            if not ignore_path.exists():
                continue
            try:
                content = ignore_path.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                negated = line.startswith("!")
                if negated:
                    line = line[1:]
                elif line.startswith("\\!"):
                    line = line[1:]
                if line.startswith("/"):
                    line = line[1:]
                if not line:
                    continue
                self._rules.append((negated, line))

    def ignores(self, rel_path: str) -> bool:
        """True if the relative path is ignored (last matching rule wins)."""
        ignored = False
        for negated, pattern in self._rules:
            pat = pattern.rstrip("/")
            if (
                fnmatch.fnmatch(rel_path, pat)
                or fnmatch.fnmatch(rel_path, f"{pat}/")
                or fnmatch.fnmatch(rel_path, f"{pat}/**")
            ):
                ignored = not negated
        return ignored


class SkillStorage:
    """File-based skill storage."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)

    def load_skills(self) -> list[Skill]:
        skills, _ = self.load_skills_with_diagnostics()
        return skills

    def load_skills_with_diagnostics(self) -> tuple[list[Skill], list[dict[str, Any]]]:
        """Load all skills, returning (skills, diagnostics).

        Diagnostics are Pi-style warning dicts
        (``{"type": "warning", "message": ..., "path": ...}``) for skills
        that loaded despite validation issues, plus entries for skills
        that were skipped entirely.
        """
        skills: list[Skill] = []
        diagnostics: list[dict[str, Any]] = []

        if not self.base_dir.exists():
            return skills, diagnostics

        ignore_matcher = _IgnoreMatcher(self.base_dir)

        for item in self.base_dir.iterdir():
            if not item.is_dir():
                continue

            rel_path = f"{item.name}/"
            if ignore_matcher.ignores(rel_path):
                continue

            skill_file = item / "SKILL.md"
            skill, file_diagnostics = parse_skill_file_with_diagnostics(skill_file)
            diagnostics.extend(file_diagnostics)

            if skill:
                skills.append(skill)

        return skills, diagnostics

    def load_skill(self, skill_name: str) -> Skill | None:
        if not _is_valid_skill_name(skill_name):
            return None

        # Fast path: directory named after the skill
        skill_dir = self.base_dir / skill_name
        skill_file = skill_dir / "SKILL.md"

        base_dir = self.base_dir.resolve()
        resolved = skill_file.resolve()
        if resolved.is_relative_to(base_dir):
            skill, _ = parse_skill_file_with_diagnostics(skill_file)
            if skill and skill["name"] == skill_name:
                return skill

        # Fallback: scan for a skill whose (frontmatter) name matches, so
        # skills whose name differs from their directory stay loadable.
        if not self.base_dir.exists():
            return None
        for item in self.base_dir.iterdir():
            if not item.is_dir():
                continue
            candidate, _ = parse_skill_file_with_diagnostics(item / "SKILL.md")
            if candidate and candidate["name"] == skill_name:
                return candidate
        return None

    def list_skills(self) -> list[str]:
        skills = self.load_skills()
        return [s["name"] for s in skills]


class SystemSkillStorage(SkillStorage):
    """Storage for bundled seed skills."""

    def __init__(self, base_dir: str | Path = "seeds/skills"):
        super().__init__(base_dir)


class UserSkillStorage(SkillStorage):
    """Storage for user-specific skills."""

    def __init__(self, user_id: str, base_dir: str | Path | None = None):
        if base_dir:
            storage_dir = Path(base_dir)
        else:
            from src.storage.paths import get_paths

            storage_dir = get_paths(user_id).user_skills_dir()

        super().__init__(storage_dir)
        self.user_id = user_id
