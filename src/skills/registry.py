"""Skill registry — user-level storage for runtime skills.

Bundled seed skills (src/skills_seed/) are seeded to the user's skills directory on first
run. workspace_id and workspace skill directories are accepted for compatibility but ignored
at runtime.
"""

import threading
from pathlib import Path

from src.skills.models import Skill, _is_valid_skill_name
from src.skills.storage import SkillStorage

_registries: dict[str, "SkillRegistry"] = {}
_lock = threading.Lock()


def get_skill_registry(
    user_id: str = "default_user", workspace_id: str = "personal"
) -> "SkillRegistry":
    """Get or create a cached user-level SkillRegistry.

    All code should use this factory instead of constructing SkillRegistry
    directly, to ensure a single cached instance per user. workspace_id is
    accepted for compatibility and ignored at runtime.
    """
    uid = user_id or "default_user"
    cache_key = uid
    with _lock:
        if cache_key not in _registries:
            _registries[cache_key] = SkillRegistry(
                user_id=uid, workspace_id="personal"
            )
        return _registries[cache_key]


def reset_skill_registries() -> None:
    """Clear all cached registries (useful for testing)."""
    with _lock:
        _registries.clear()


class SkillRegistry:
    """Registry for user-level skills.

    On first run, bundled seed skills are seeded from src/skills_seed/ to the user's skills
    directory. workspace_id and workspace_skills_dir are compatibility-only.
    """

    def __init__(
        self,
        skills_dir: str | Path | None = None,
        workspace_skills_dir: str | Path | None = None,
        user_id: str | None = None,
        workspace_id: str = "personal",
    ):
        from src.storage.paths import DataPaths

        paths = DataPaths(user_id=user_id, workspace_id=workspace_id)
        self.workspace_id = workspace_id

        self.skills_dir = Path(skills_dir) if skills_dir else paths.user_skills_dir()
        self.storage = SkillStorage(self.skills_dir)

        self.workspace_skills_dir = (
            Path(workspace_skills_dir) if workspace_skills_dir else paths.workspace_skills_dir()
        )
        self._loaded_skills: dict[str, int] = {}
        self._seeded = False

    def _seed_system_skills(self) -> None:
        """Copy bundled seed skills to user skills directory on first run."""
        if self._seeded:
            return
        self._seeded = True

        import shutil

        seed_marker = self.skills_dir / ".skills_seeded"
        if seed_marker.exists():
            return

        system_src = Path("src/skills_seed")
        if not system_src.exists():
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            seed_marker.write_text("", encoding="utf-8")
            return

        self.skills_dir.mkdir(parents=True, exist_ok=True)

        for item in system_src.iterdir():
            if not item.is_dir():
                continue
            dest = self.skills_dir / item.name
            if not dest.exists():
                shutil.copytree(item, dest)

        seed_marker.write_text("", encoding="utf-8")

    def reload(self) -> None:
        """Reload all skills (clear cache, re-seed system skills)."""
        self._seeded = False
        self._loaded_skills.clear()

    def mark_skill_loaded(self, skill_name: str) -> None:
        """Track that a skill has been loaded into context (increment count)."""
        self._loaded_skills[skill_name] = self._loaded_skills.get(skill_name, 0) + 1

    def get_loaded_skills(self) -> list[str]:
        """Get list of skills loaded in current session."""
        return list(self._loaded_skills.keys())

    def get_load_count(self, skill_name: str) -> int:
        """Get how many times a skill has been loaded (0 if never loaded)."""
        return self._loaded_skills.get(skill_name, 0)

    def get_all_skills(self) -> list[Skill]:
        """Get all user-level available skills."""
        self._seed_system_skills()
        user_skills = self.storage.load_skills()

        for s in user_skills:
            if "metadata" not in s:
                s["metadata"] = {}
            s["metadata"]["scope"] = "user"
            s["metadata"]["workspace_id"] = ""

        return user_skills

    def get_skill(self, skill_name: str) -> Skill | None:
        """Get a specific skill by name (workspace overrides user)."""
        if not _is_valid_skill_name(skill_name):
            return None

        self._seed_system_skills()

        user_skill = self.storage.load_skill(skill_name)
        if user_skill:
            if "metadata" not in user_skill:
                user_skill["metadata"] = {}
            user_skill["metadata"]["scope"] = "user"
            user_skill["metadata"]["workspace_id"] = ""
            return user_skill

        return None

    def list_skills(self) -> list[str]:
        """List all available skill names."""
        skills = self.get_all_skills()
        return [s["name"] for s in skills]

    def search_skills(self, query: str) -> list[Skill]:
        """Search for skills matching a query string."""
        query_lower = query.lower()
        all_skills = self.get_all_skills()
        return [
            s
            for s in all_skills
            if query_lower in s["name"].lower()
            or query_lower in s.get("description", "").lower()
            or query_lower in s.get("content", "").lower()
        ]

    def get_skill_descriptions(self, include_disabled: bool = False) -> list[str]:
        """Get formatted skill descriptions for system prompt.

        Args:
            include_disabled: If True, include skills with disable_model_invocation.
                              If False, exclude them from the agent's discovery list.
        """
        from src.skills.models import skill_to_system_prompt_entry

        skills = self.get_all_skills()
        if not include_disabled:
            skills = [
                s for s in skills
                if s.get("metadata", {}).get("disable_model_invocation", "").lower() not in ("true", "1", "yes")
            ]
        return [skill_to_system_prompt_entry(s) for s in skills]
