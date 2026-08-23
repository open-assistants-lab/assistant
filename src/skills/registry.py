"""Skill registry — user-level storage for runtime skills.

Bundled seed skills (seeds/skills/) are seeded to the user's skills directory on first
run. workspace_id and workspace skill directories are accepted for compatibility but ignored
at runtime.
"""

import threading
from pathlib import Path
from typing import Any

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

    On first run, bundled seed skills are seeded from seeds/skills/ to the user's skills
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
        self._diagnostics: list[dict[str, Any]] = []
        self._seeded = False

    def _seed_system_skills(self) -> None:
        """Copy bundled seed skills to user skills directory on first run.

        Re-copies a seed skill when the seed content changed AND the user's
        copy is still the previously seeded version (tracked via a sidecar
        hash file). User-modified copies are never overwritten.
        """
        if self._seeded:
            return
        self._seeded = True

        import hashlib
        import shutil

        seed_marker = self.skills_dir / ".skills_seeded"
        system_src = Path("seeds/skills")
        if not system_src.exists():
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            seed_marker.write_text("", encoding="utf-8")
            return

        # Legacy fast path: marker exists and no sidecar-tracked skills →
        # seeded before sidecars existed; leave untouched (no refresh).
        if seed_marker.exists() and not any(self.skills_dir.glob("*/.seed-hash")):
            return

        self.skills_dir.mkdir(parents=True, exist_ok=True)

        def _content_hash(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        for item in system_src.iterdir():
            if not item.is_dir():
                continue
            seed_file = item / "SKILL.md"
            if not seed_file.exists():
                continue
            dest = self.skills_dir / item.name
            dest_file = dest / "SKILL.md"
            sidecar = dest / ".seed-hash"
            seed_hash = _content_hash(seed_file)

            if not dest.exists():
                shutil.copytree(item, dest)
                sidecar.write_text(seed_hash, encoding="utf-8")
                continue

            if not dest_file.exists():
                continue
            if not sidecar.exists():
                # Pre-sidecar copy: leave untouched, record current seed hash
                sidecar.write_text(seed_hash, encoding="utf-8")
                continue
            if sidecar.read_text(encoding="utf-8").strip() == seed_hash:
                continue  # already up to date
            if _content_hash(dest_file) != sidecar.read_text(encoding="utf-8").strip():
                # User modified their copy — never overwrite; stop trying
                sidecar.write_text(seed_hash, encoding="utf-8")
                continue
            # Untouched stale copy — refresh from seed
            shutil.copytree(item, dest, dirs_exist_ok=True)
            sidecar.write_text(seed_hash, encoding="utf-8")

        seed_marker.write_text("", encoding="utf-8")

    def reload(self) -> None:
        """Reload all skills (clear cache, re-seed system skills)."""
        self._seeded = False
        self._loaded_skills.clear()
        self._diagnostics.clear()

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
        """Get all user-level available skills.

        Deduplicates skills that resolve to the same file (symlinks) and
        records name-collision diagnostics (first skill wins).
        """
        self._seed_system_skills()
        user_skills, diagnostics = self.storage.load_skills_with_diagnostics()
        self._diagnostics = list(diagnostics)

        skill_map: dict[str, Skill] = {}
        real_paths: set[str] = set()
        for s in user_skills:
            real_path = str(Path(s.get("path", "")).resolve())
            if real_path in real_paths:
                continue
            real_paths.add(real_path)
            name = s.get("name", "")
            existing = skill_map.get(name)
            if existing is not None:
                self._diagnostics.append(
                    {
                        "type": "collision",
                        "message": f'name "{name}" collision',
                        "path": s.get("path", ""),
                        "collision": {
                            "resourceType": "skill",
                            "name": name,
                            "winnerPath": existing.get("path", ""),
                            "loserPath": s.get("path", ""),
                        },
                    }
                )
                continue
            skill_map[name] = s

        for s in skill_map.values():
            if "metadata" not in s:
                s["metadata"] = {}
            s["metadata"]["scope"] = "user"
            s["metadata"]["workspace_id"] = ""

        return list(skill_map.values())

    def get_diagnostics(self) -> list[dict[str, Any]]:
        """Validation diagnostics from the last get_all_skills() call.

        Entries are ``{"type": "warning" | "collision", ...}`` dicts.
        """
        return list(self._diagnostics)

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
                if str(s.get("metadata", {}).get("disable_model_invocation", "")).lower()
                not in ("true", "1", "yes")
            ]
        return [skill_to_system_prompt_entry(s) for s in skills]
