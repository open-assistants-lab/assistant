"""Skill registry — user-level storage for runtime skills.

Bundled seed skills (seeds/skills/) are seeded to the user's skills directory on first
run. workspace_id and workspace skill directories are accepted for compatibility but ignored
at runtime.
"""

import hashlib
import json
import re
import shutil
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.app_logging import get_logger
from src.skills.models import (
    Skill,
    _is_valid_skill_name,
    parse_skill_file_with_diagnostics,
)
from src.skills.storage import SkillStorage
from src.storage.paths import DEFAULT_USER_ID

logger = get_logger()

_registries: dict[str, "SkillRegistry"] = {}
_lock = threading.Lock()


class DraftConflictError(Exception):
    """Cannot approve a draft because a live skill of the same name exists."""


def get_skill_registry(
    user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal"
) -> "SkillRegistry":
    """Get or create a cached user-level SkillRegistry.

    All code should use this factory instead of constructing SkillRegistry
    directly, to ensure a single cached instance per user. workspace_id is
    accepted for compatibility and ignored at runtime.
    """
    uid = user_id or DEFAULT_USER_ID
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


def _seed_files(item: Path) -> dict[str, str]:
    """Relative path -> sha256 for every file the seed ships (excluding our own
    bookkeeping sidecars)."""
    out: dict[str, str] = {}
    for path in item.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(item))
        if rel.startswith(".") or rel in (".seed-hash", ".seed-manifest.json"):
            continue
        try:
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def _write_seed_manifest(
    dest: Path,
    item: Path,
    content_hash: "Callable[[Path], str] | None" = None,
    manifest: dict[str, str] | None = None,
) -> None:
    manifest = manifest if manifest is not None else _seed_files(item)
    (dest / ".seed-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )


def _read_seed_manifest(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


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
        # Drafts live OUTSIDE the scanned skills dir so they never appear in
        # get_all_skills()/get_skill() until explicitly approved (P1-T6/T8:
        # human review gate — auto-drafted skills are never runtime-visible).
        self.drafts_dir = self.skills_dir.parent / ".skill-drafts"
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

        seed_names: set[str] = set()
        for item in sorted(system_src.iterdir()):
            if not item.is_dir():
                continue
            seed_file = item / "SKILL.md"
            if not seed_file.exists():
                continue
            seed_names.add(item.name)
            dest = self.skills_dir / item.name
            dest_file = dest / "SKILL.md"
            sidecar = dest / ".seed-hash"
            manifest_file = dest / ".seed-manifest.json"

            if not dest.exists():
                shutil.copytree(item, dest)
                _write_seed_manifest(dest, item, _content_hash)
                sidecar.write_text(_content_hash(seed_file), encoding="utf-8")
                continue

            if not dest_file.exists():
                continue
            if not sidecar.exists():
                # Pre-sidecar copy: leave untouched, record current seed hash
                _write_seed_manifest(dest, item, _content_hash)
                sidecar.write_text(_content_hash(seed_file), encoding="utf-8")
                continue

            # Per-file refresh (#91). The previous whole-directory copytree
            # refreshed only when SKILL.md changed, so a resource-only seed
            # change was missed, and copytree(dirs_exist_ok=True) OVERWROTE
            # resource files the user had edited. Each seeded file is compared
            # against the manifest recorded at seed time: unchanged upstream
            # files are refreshed, user-modified files are kept.
            manifest = _read_seed_manifest(manifest_file)
            if not manifest:
                manifest = {"SKILL.md": sidecar.read_text(encoding="utf-8").strip()}
                _write_seed_manifest(dest, item, _content_hash, manifest)
            refreshed = False
            for rel, upstream_hash in _seed_files(item).items():
                target = dest / rel
                recorded = manifest.get(rel)
                if target.exists() and recorded and _content_hash(target) != recorded:
                    continue  # user edited this file; never overwrite
                if not target.exists() or _content_hash(target) != upstream_hash:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item / rel, target)
                    refreshed = True
            # Files removed upstream but still recorded: drop only if untouched.
            for rel in list(manifest):
                if rel in _seed_files(item):
                    continue
                target = dest / rel
                if target.exists() and _content_hash(target) == manifest[rel]:
                    target.unlink()
                    refreshed = True
            _write_seed_manifest(dest, item, _content_hash)
            sidecar.write_text(_content_hash(seed_file), encoding="utf-8")
            if refreshed:
                logger.info("skills.seed_refreshed", {"skill": item.name})

        # #99 — a seed removed upstream must not return on the next reload,
        # which it did whenever any other tracked seed kept the fast path busy.
        for tracked in sorted(self.skills_dir.glob("*/.seed-hash")):
            if tracked.parent.name in seed_names:
                continue
            manifest = _read_seed_manifest(tracked.parent / ".seed-manifest.json")
            if any(
                (tracked.parent / rel).exists() and _content_hash(tracked.parent / rel) != h
                for rel, h in manifest.items()
            ):
                continue  # user edited it since seeding; keep the skill
            shutil.rmtree(tracked.parent)

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

    # -- Skill review queue (drafts) ---------------------------------------

    def _draft_dir(self, name: str) -> Path:
        # Draft-name validation hoisted here so approve/reject/get inherit it —
        # reject_skill_draft rmtree's the dir, so an unvalidated traversal name
        # ("../../../x") could delete outside drafts_dir (review finding P2).
        if not re.fullmatch(r"[a-z0-9-]+", name):
            raise ValueError(
                f"invalid draft name {name!r}: must match [a-z0-9-]+"
            )
        return self.drafts_dir / name

    def put_skill_draft(self, name: str, content: str, *, source: str = "") -> Path:
        """Write (or replace) a skill draft in the review queue.

        Args:
            name: skill name (Agent Skills spec: lowercase a-z 0-9 hyphens).
            content: full SKILL.md file content (frontmatter + body).
            source: optional provenance (e.g. the URL it was extracted from).

        Returns the draft SKILL.md path.
        """
        if not re.fullmatch(r"[a-z0-9-]+", name or ""):
            raise ValueError(
                f"invalid draft skill name {name!r}: lowercase a-z, 0-9, hyphens"
            )
        draft_dir = self._draft_dir(name)
        draft_dir.mkdir(parents=True, exist_ok=True)
        (draft_dir / "SKILL.md").write_text(content, encoding="utf-8")
        (draft_dir / ".draft-meta.json").write_text(
            json.dumps(
                {"source": source, "drafted_at": datetime.now(UTC).isoformat()}
            ),
            encoding="utf-8",
        )
        return draft_dir / "SKILL.md"

    def list_skill_drafts(self) -> list[dict[str, Any]]:
        """List pending drafts: {name, path, source} entries, name-sorted."""
        if not self.drafts_dir.exists():
            return []
        drafts = []
        for item in sorted(self.drafts_dir.iterdir()):
            meta_file = item / ".draft-meta.json"
            meta: dict[str, Any] = {}
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    meta = {}
            drafts.append(
                {
                    "name": item.name,
                    "path": str(item / "SKILL.md"),
                    "source": str(meta.get("source", "")),
                }
            )
        return drafts

    def get_skill_draft(self, name: str) -> Skill | None:
        """Parse a pending draft via the standard frontmatter parser."""
        draft_skill_md = self._draft_dir(name) / "SKILL.md"
        if not draft_skill_md.exists():
            return None
        from src.skills.models import parse_skill_file

        return parse_skill_file(draft_skill_md)

    def approve_skill_draft(self, name: str) -> Path:
        """Promote a draft into the live skills dir (human review passed).

        Raises FileNotFoundError if no such draft; FileExistsError when a
        live skill (possibly user-customized) already occupies the name.
        """
        draft_dir = self._draft_dir(name)
        draft_file = draft_dir / "SKILL.md"
        if not draft_file.exists():
            raise FileNotFoundError(f"no draft named {name!r}")
        # #101 — validate BEFORE moving: an unparsable draft used to be promoted
        # into the live skills dir, where discovery skipped it, leaving the
        # review queue empty and a dead directory behind.
        draft_skill, draft_diagnostics = parse_skill_file_with_diagnostics(draft_file)
        if draft_skill is None:
            raise ValueError(
                f"draft {name!r} is not a loadable skill (missing/empty description, "
                "invalid frontmatter, or unreadable file): "
                + "; ".join(str(d.get("message", "")) for d in draft_diagnostics)
            )
        # #93 — a live skill can live under a DIFFERENT directory with the same
        # logical name; check the name, not just the path.
        logical_name = str(draft_skill.get("name") or name)
        existing = self.storage.load_skill(logical_name) if hasattr(self, "storage") else None
        if existing is not None:
            raise FileExistsError(
                f"live skill {logical_name!r} already exists at "
                f"{existing.get('path', '')}; resolve manually (reject or edit it)"
            )
        target_dir = self.skills_dir / name
        if target_dir.exists():
            raise FileExistsError(
                f"live skill {name!r} already exists at {target_dir}; "
                "resolve manually (reject or edit the existing skill)"
            )
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(draft_dir), str(target_dir))
        meta_file = target_dir / ".draft-meta.json"
        if meta_file.exists():
            meta_file.unlink()
        return target_dir / "SKILL.md"

    def reject_skill_draft(self, name: str) -> None:
        """Discard a draft (human review rejected or superseded)."""
        draft_dir = self._draft_dir(name)
        if draft_dir.exists():
            shutil.rmtree(draft_dir)

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
