"""Skill models and schema for Agent Skills compatibility."""

import re
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import yaml


class SkillMetadata(TypedDict):
    """Skill metadata from YAML frontmatter."""

    name: str
    description: str
    license: NotRequired[str]
    compatibility: NotRequired[str]
    metadata: NotRequired[dict[str, str]]
    allowed_tools: NotRequired[str]


class Skill(TypedDict):
    """A skill that can be progressively disclosed to the agent.

    Based on Agent Skills spec: https://agentskills.io/specification
    """

    name: str
    description: str
    content: str
    path: str
    license: NotRequired[str]
    compatibility: NotRequired[str]
    metadata: NotRequired[dict[str, str]]
    allowed_tools: NotRequired[str]


def parse_skill_file(skill_path: Path) -> Skill | None:
    """Parse a SKILL.md file and extract metadata and content.

    Args:
        skill_path: Path to SKILL.md file

    Returns:
        Skill dict with metadata and content, or None if invalid
    """
    skill, _ = parse_skill_file_with_diagnostics(skill_path)
    return skill


def parse_skill_file_with_diagnostics(
    skill_path: Path,
) -> tuple[Skill | None, list[dict[str, Any]]]:
    """Parse a SKILL.md file, returning the skill and validation diagnostics.

    Warnings-not-errors semantics (Pi-style):
    - invalid frontmatter name falls back to the parent directory name
    - over-long descriptions still load (with a warning)
    - only a missing/empty description prevents loading

    Returns:
        (skill, diagnostics) where diagnostics is a list of
        {"type": "warning", "message": ..., "path": ...} dicts.
    """
    diagnostics: list[dict[str, Any]] = []
    if not skill_path.exists():
        return None, diagnostics

    content = skill_path.read_text(encoding="utf-8")

    # Split by YAML frontmatter delimiter
    if not content.startswith("---"):
        return None, diagnostics

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None, diagnostics

    frontmatter = parts[1].strip()
    body = parts[2].strip() if len(parts) > 2 else ""

    # Parse YAML frontmatter
    try:
        metadata = yaml.safe_load(frontmatter)
    except yaml.YAMLError:
        return None, diagnostics

    if not isinstance(metadata, dict):
        return None, diagnostics

    # Validate required fields
    name = metadata.get("name")
    description = metadata.get("description")

    if not isinstance(name, str) or not name.strip():
        name = None
    if not isinstance(description, str) or not description.strip():
        description = None

    # Name: fall back to parent directory name when frontmatter name is
    # missing or invalid (warnings, not errors).
    parent_dir_name = skill_path.parent.name
    if name is None:
        name = parent_dir_name
    name_errors = validate_skill_name(name)
    if name_errors:
        if name != parent_dir_name:
            diagnostics.append(
                {
                    "type": "warning",
                    "message": f"invalid skill name {name!r}: {'; '.join(name_errors)}; "
                    f"falling back to directory name {parent_dir_name!r}",
                    "path": str(skill_path),
                }
            )
            name = parent_dir_name
        else:
            diagnostics.append(
                {
                    "type": "warning",
                    "message": f"invalid skill name {name!r}: {'; '.join(name_errors)}",
                    "path": str(skill_path),
                }
            )

    # Description: required to load; over-long still loads with a warning.
    if description is None:
        diagnostics.append(
            {
                "type": "warning",
                "message": "description is required",
                "path": str(skill_path),
            }
        )
        return None, diagnostics
    for error in validate_skill_description(description):
        diagnostics.append(
            {"type": "warning", "message": error, "path": str(skill_path)}
        )

    skill: Skill = {
        "name": name,
        "description": description,
        "content": body,
        "path": str(skill_path.parent),
    }

    # Optional fields
    if license := metadata.get("license"):
        skill["license"] = license
    if compatibility := metadata.get("compatibility"):
        skill["compatibility"] = compatibility
    if metadata_dict := metadata.get("metadata"):
        skill["metadata"] = metadata_dict
    if allowed_tools := metadata.get("allowed-tools"):
        skill["allowed_tools"] = allowed_tools

    return skill, diagnostics


def validate_skill_name(name: str) -> list[str]:
    """Validate a skill name per the Agent Skills spec.

    Returns a list of validation error messages (empty if valid).
    """
    errors: list[str] = []
    if not name:
        errors.append("name is required")
        return errors
    if len(name) > 64:
        errors.append(f"name exceeds 64 characters ({len(name)})")
    if not re.fullmatch(r"[a-z0-9-]+", name):
        errors.append("name must be lowercase a-z, 0-9, hyphens only")
    if name.startswith("-") or name.endswith("-"):
        errors.append("name must not start or end with a hyphen")
    if "--" in name:
        errors.append("name must not contain consecutive hyphens")
    return errors


def validate_skill_description(description: str) -> list[str]:
    """Validate a skill description per the Agent Skills spec.

    Returns a list of validation error messages (empty if valid).
    """
    if not description or not description.strip():
        return ["description is required"]
    if len(description) > 1024:
        return [f"description exceeds 1024 characters ({len(description)})"]
    return []


def _is_valid_skill_name(name: str) -> bool:
    """Validate skill name format.

    Must be 1-64 characters, lowercase letters and hyphens only,
    cannot start or end with hyphen, no consecutive hyphens.
    """
    if not name or len(name) > 64:
        return False
    if name[0] == "-" or name[-1] == "-":
        return False
    if "--" in name:
        return False
    return all(c.islower() or c.isdigit() or c == "-" for c in name)


def skill_to_system_prompt_entry(skill: Skill) -> str:
    """Convert skill to system prompt entry (name + description).

    Args:
        skill: Skill dict

    Returns:
        Formatted string for system prompt
    """
    return f"- **{skill['name']}**: {skill['description']}"
