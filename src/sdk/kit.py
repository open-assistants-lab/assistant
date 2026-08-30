"""Kit factory (P1-T7): vertical kits are CONTENT, not code.

A kit bundles an agent template (PROFILE.md), methodology skills, a review
rubric, and an eval set. kit_validate checks all content before kit_install
writes anything, so a malformed kit can never partially install (plan §P1-T7
non-negotiable: validate-then-write, never partial-install).

Install targets (subagent path per plan):
- PROFILE.md  -> user_subagents_dir/<kit>/PROFILE.md (coordinator.load_def)
- skills      -> SkillRegistry drafts, auto-approved (installing admin
                 content IS the explicit approval act)
- rubrics     -> data/private/kits/<kit>/rubrics/
- eval set    -> data/private/kits/<kit>/eval_set.yaml (mirror of kit dir)
An install manifest (kit.json) records exact paths for orphan-free uninstall.
"""

import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from src.app_logging import get_logger

logger = get_logger()

KIT_MANIFEST = "kit.yaml"


class KitError(Exception):
    """Raised when a kit is invalid or install state is inconsistent."""


def _manifest_tools(meta: dict[str, Any], problems: list[str]) -> list[str]:
    """Validate and return the manifest's optional tools.enable list."""
    tools = meta.get("tools")
    if tools is None:
        return []
    if not isinstance(tools, dict) or not isinstance(tools.get("enable", []), list):
        problems.append(f"{KIT_MANIFEST}: 'tools.enable' must be a list")
        return []
    enabled = tools.get("enable", [])
    if not all(isinstance(name, str) and name for name in enabled):
        problems.append(f"{KIT_MANIFEST}: 'tools.enable' entries must be tool names")
        return []

    from src.sdk.native_tools import get_native_tool_names

    unknown = sorted(set(enabled) - get_native_tool_names())
    for name in unknown:
        problems.append(f"{KIT_MANIFEST}: unknown tool in tools.enable: {name}")
    return list(dict.fromkeys(enabled))


def _saved_tool_scopes(state_dir: Path) -> dict[str, bool | None]:
    """Load exact pre-install settings (None means the key was absent)."""
    manifest_file = state_dir / "kit.json"
    if not manifest_file.exists():
        return {}
    state = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
    scopes = state.get("previous_tool_scopes", {})
    if not isinstance(scopes, dict):
        return {}
    return {name: value if isinstance(value, bool) else None for name, value in scopes.items()}


def _kit_problems(kit_dir: Path) -> list[str]:
    """Collect every validation problem (empty list = valid kit)."""
    problems: list[str] = []
    if not kit_dir.is_dir():
        return [f"kit dir not found: {kit_dir}"]

    manifest = kit_dir / KIT_MANIFEST
    if not manifest.exists():
        problems.append(f"missing {KIT_MANIFEST}")
        return problems
    try:
        meta = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        problems.append(f"{KIT_MANIFEST} is not valid YAML: {e}")
        return problems
    name = meta.get("name")
    if not isinstance(name, str) or not name or not name.isascii() or " " in name:
        problems.append(f"{KIT_MANIFEST}: 'name' must be a slug, got {name!r}")
    elif name != kit_dir.name:
        problems.append(f"{KIT_MANIFEST}: name {name!r} != dir name {kit_dir.name!r}")
    if not meta.get("description"):
        problems.append(f"{KIT_MANIFEST}: 'description' required")
    _manifest_tools(meta, problems)

    profile = kit_dir / "PROFILE.md"
    if not profile.exists():
        problems.append("missing PROFILE.md (agent template)")
    else:
        try:
            from agentprofile.parser import load_profile as _load_ap

            _load_ap(str(profile))
        except Exception as e:  # noqa: BLE001 — any parse failure is a kit error
            problems.append(f"PROFILE.md unparseable: {e}")

    skills_dir = kit_dir / "skills"
    if not skills_dir.is_dir() or not any(skills_dir.iterdir()):
        problems.append("skills/ must contain at least one skill dir")
    else:
        for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                problems.append(f"skills/{skill_dir.name}/ missing SKILL.md")
                continue
            head = skill_md.read_text(encoding="utf-8", errors="replace").split(
                "---", 2
            )
            fm: dict[str, Any] = {}
            if len(head) >= 3:
                try:
                    fm = yaml.safe_load(head[1]) or {}
                except yaml.YAMLError:
                    pass
            if fm.get("name") != skill_dir.name:
                problems.append(
                    f"skills/{skill_dir.name}/SKILL.md frontmatter name "
                    f"{fm.get('name')!r} != dir name {skill_dir.name!r}"
                )
            if not fm.get("description"):
                problems.append(
                    f"skills/{skill_dir.name}/SKILL.md missing description"
                )

    rubrics = kit_dir / "rubrics"
    if not rubrics.is_dir() or not list(rubrics.glob("*.md")):
        problems.append("rubrics/ must contain at least one .md rubric")

    personas_file = kit_dir / "eval" / "personas.yaml"
    if not personas_file.exists():
        problems.append("eval/personas.yaml missing (kit eval set)")
    else:
        try:
            data = yaml.safe_load(personas_file.read_text(encoding="utf-8")) or {}
            personas = data.get("personas")
            if not isinstance(personas, list) or not personas:
                problems.append("eval/personas.yaml: 'personas' list required")
            else:
                for p in personas:
                    if not isinstance(p, dict) or not p.get("id") or not p.get("style"):
                        problems.append(
                            "eval/personas.yaml: each persona needs id+style "
                            f"(got {p})"
                        )
        except yaml.YAMLError as e:
            problems.append(f"eval/personas.yaml invalid YAML: {e}")

    return problems


def kit_validate(kit_dir: str | Path) -> list[str]:
    """Validate a kit dir. Returns list of problems; empty = valid."""
    return _kit_problems(Path(kit_dir))


def kit_list(kits_root: str | Path = "kits") -> list[dict[str, Any]]:
    """List kits under kits_root with validity info."""
    root = Path(kits_root)
    entries: list[dict[str, Any]] = []
    if not root.is_dir():
        return entries
    for kit_dir in sorted(root.iterdir()):
        if not kit_dir.is_dir():
            continue
        entries.append(
            {
                "name": kit_dir.name,
                "path": str(kit_dir),
                "valid": _kit_problems(kit_dir) == [],
            }
        )
    return entries


def _state_dir(name: str, user_id: str) -> Path:
    from src.storage.paths import get_paths

    paths = get_paths(user_id=user_id, workspace_id="personal")
    return paths.user_dir / "private" / "kits" / name


def kit_install(kit_dir: str | Path, user_id: str = "default_user") -> dict[str, Any]:
    """Install a validated kit: agent template, skills, rubrics, eval set.

    Skills go through the review-queue draft API and are immediately
    approved — installing admin-authored kit content is itself the explicit
    approval act (auto-DRAFTED skills still require human review; kit skills
    are curated content).
    """
    from src.skills.registry import SkillRegistry
    from src.storage.paths import get_paths

    kit_dir = Path(kit_dir)
    problems = _kit_problems(kit_dir)
    if problems:
        raise KitError(
            "kit validation failed (nothing installed):\n- " + "\n- ".join(problems)
        )
    meta = yaml.safe_load((kit_dir / KIT_MANIFEST).read_text(encoding="utf-8")) or {}
    name = kit_dir.name

    paths = get_paths(user_id=user_id, workspace_id="personal")
    state_dir = _state_dir(name, user_id)
    profile_target_dir = paths.user_subagents_dir() / name
    # never partial-install: all copy operations happen AFTER validation, but
    # still build into the state dir then perform installs; on any failure,
    # roll back what was written.
    installed_skills: list[str] = []
    # Reinstall safety: back up any live skill before replacing it so a
    # mid-install failure restores the (possibly hand-customized) previous
    # copy instead of losing it to the rollback (review P1, T7).
    backups: dict[str, Path] = {}
    registry = SkillRegistry(user_id=user_id, workspace_id="personal")
    backup_root = Path(tempfile.mkdtemp(prefix="kit-install-backup-"))
    from src.sdk.capabilities import load_user_capabilities, set_resource_enabled

    tools_to_enable = _manifest_tools(meta, [])
    existing_previous_scopes = _saved_tool_scopes(state_dir)
    current_caps = load_user_capabilities(user_id).get("tools", {})
    current_caps = current_caps if isinstance(current_caps, dict) else {}
    affected_tools = set(tools_to_enable) | set(existing_previous_scopes)
    rollback_scopes = {tool: current_caps.get(tool) for tool in affected_tools}
    previous_tool_scopes = {
        tool: existing_previous_scopes.get(tool, current_caps.get(tool))
        for tool in tools_to_enable
    }
    try:
        # A reinstall may remove a previously requested tool. Restore that
        # tool's original setting before applying the new manifest.
        for tool in set(existing_previous_scopes) - set(tools_to_enable):
            set_resource_enabled(user_id, "tools", tool, existing_previous_scopes[tool])
        for tool in tools_to_enable:
            set_resource_enabled(user_id, "tools", tool, True)

        profile_target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(kit_dir / "PROFILE.md", profile_target_dir / "PROFILE.md")

        for skill_dir in sorted(p for p in (kit_dir / "skills").iterdir() if p.is_dir()):
            # reinstall = replace (idempotent): move the live copy to a temp
            # backup first so approve_skill_draft's FileExistsError guard
            # doesn't fire — the backup is the rollback source on failure
            existing = registry.skills_dir / skill_dir.name
            if existing.exists():
                try:
                    previous = (existing / "SKILL.md").read_text(encoding="utf-8")
                except OSError:
                    previous = ""
                if previous and previous != (
                    skill_dir / "SKILL.md").read_text(encoding="utf-8"):
                    # user modified the live copy since install — overwriting
                    # discards their edits; warn loudly (hash-guard upgrade
                    # tracked in deferred followups)
                    logger.warning(
                        "kit_reinstall_overwrites_modified_skill",
                        {"kit": name, "skill": skill_dir.name},
                        user_id=user_id,
                    )
                backup = backup_root / skill_dir.name
                shutil.copytree(existing, backup)
                backups[skill_dir.name] = backup
                shutil.rmtree(existing)
            content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            registry.put_skill_draft(
                skill_dir.name, content, source=f"kit:{name}"
            )
            registry.approve_skill_draft(skill_dir.name)
            installed_skills.append(skill_dir.name)

        rubric_dir = state_dir / "rubrics"
        rubric_dir.mkdir(parents=True, exist_ok=True)
        for rubric in (kit_dir / "rubrics").glob("*.md"):
            shutil.copyfile(rubric, rubric_dir / rubric.name)

        eval_src = kit_dir / "eval" / "personas.yaml"
        state_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(eval_src, state_dir / "eval_set.yaml")

        (state_dir / "kit.json").write_text(
            yaml.safe_dump(
                {
                    "name": name,
                    "description": meta.get("description", ""),
                    "version": meta.get("version", ""),
                    "profile_path": str(profile_target_dir / "PROFILE.md"),
                    "skills": installed_skills,
                    "rubrics": [r.name for r in rubric_dir.glob("*.md")],
                    "eval_set": str(eval_src),
                    "previous_tool_scopes": previous_tool_scopes,
                }
            ),
            encoding="utf-8",
        )
    except Exception: # noqa: BLE001
        # roll back partial writes so a failed install leaves nothing;
        # pre-existing live skills come back from the backup untouched
        for skill in installed_skills:
            _remove_skill(registry, skill)
        for skill, backup in backups.items():
            target = registry.skills_dir / skill
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(backup, target)
        shutil.rmtree(state_dir, ignore_errors=True)
        shutil.rmtree(profile_target_dir, ignore_errors=True)
        shutil.rmtree(backup_root, ignore_errors=True)
        for tool, previous in rollback_scopes.items():
            set_resource_enabled(user_id, "tools", tool, previous)
        raise
    shutil.rmtree(backup_root, ignore_errors=True)

    return {
        "kit": name,
        "profile_path": profile_target_dir / "PROFILE.md",
        "installed_skills": installed_skills,
        "rubric_dir": state_dir / "rubrics",
        "eval_set": str(state_dir / "eval_set.yaml"),
    }


def _remove_skill(registry: Any, name: str) -> None:
    for candidate in (registry.skills_dir / name,):
        if candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)


def kit_uninstall(name: str, user_id: str = "default_user") -> dict[str, Any]:
    """Remove an installed kit's artifacts (profile, skills, rubrics, state)."""
    state_dir = _state_dir(name, user_id)
    manifest_file = state_dir / "kit.json"
    if manifest_file.exists():
        meta = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
    else:
        meta = {"name": name, "skills": []}
    removed: dict[str, Any] = {"profile": False, "skills": [], "state": False}

    from src.storage.paths import get_paths

    paths = get_paths(user_id=user_id, workspace_id="personal")
    from src.sdk.capabilities import set_resource_enabled

    for tool, previous in _saved_tool_scopes(state_dir).items():
        set_resource_enabled(user_id, "tools", tool, previous)
    profile_dir = paths.user_subagents_dir() / name
    if (profile_dir / "PROFILE.md").exists():
        shutil.rmtree(profile_dir)
        removed["profile"] = True

    for skill in meta.get("skills", []):
        skill_dir = paths.user_skills_dir() / skill
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            removed["skills"].append(skill)

    if state_dir.exists():
        shutil.rmtree(state_dir)
        removed["state"] = True

    return {"kit": name, "removed": removed}
