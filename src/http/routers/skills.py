# mypy: disable-error-code="assignment"
"""Skills API endpoints — user-level only."""
import shutil
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.http.auth import resolve_user_id
from src.sdk.capabilities import load_user_capabilities, resource_enabled, save_user_capabilities
from src.skills.models import _is_valid_skill_name, parse_skill_file
from src.skills.registry import get_skill_registry
from src.storage.paths import DEFAULT_USER_ID, _validate_path_id, get_paths

router = APIRouter(prefix="/skills", tags=["skills"])

SkillScope = str  # compatibility: "all" | "selected" | "none"
ScopeKind = str


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    content: str
    scope: str = "user"  # deprecated, always stored at user level


class SkillUpdateRequest(BaseModel):
    description: str | None = None
    content: str | None = None
    scope: str = "user"  # deprecated


class SkillSummary(BaseModel):
    name: str
    description: str
    scope: str = "all"
    workspace_id: str | None = None
    workspace_ids: list[str] = Field(default_factory=list)
    enabled: bool = True
    is_loaded: bool = False
    disable_model_invocation: bool = False


class SkillListResponse(BaseModel):
    skills: list[SkillSummary]


class SkillDetail(SkillSummary):
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    frontmatter: dict[str, Any] = Field(default_factory=dict)


def _validate_skill_name(name: str) -> None:
    if not _is_valid_skill_name(name):
        raise HTTPException(status_code=400, detail=f"Invalid skill name: {name!r}")


def _validate_workspace_id(workspace_id: str) -> None:
    try:
        _validate_path_id(workspace_id or "personal", "workspace_id")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _validate_user_id(user_id: str) -> None:
    try:
        _validate_path_id(user_id or DEFAULT_USER_ID, "user_id")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _reset_user_loops(user_id: str) -> None:
    from src.sdk.runner import reset_user_sdk_loops

    reset_user_sdk_loops(user_id)


def _load_user_caps(user_id: str) -> dict[str, Any]:
    return load_user_capabilities(user_id)


def _save_user_enabled(user_id: str, section: str, name: str, enabled: bool) -> None:
    caps = load_user_capabilities(user_id)
    caps.setdefault(section, {})[name] = enabled
    save_user_capabilities(user_id, caps)


def _scope_response(enabled: bool) -> tuple[SkillScope, list[str]]:
    return ("all" if enabled else "none", [])


def _resource_enabled(caps: dict[str, Any], section: str, name: str) -> bool:
    return resource_enabled(caps, section, name)


def _skill_dir(user_id: str) -> Path:
    paths = get_paths(user_id)
    return paths.user_skills_dir()


def _skill_file_path(user_id: str, skill_name: str) -> Path:
    root = _skill_dir(user_id)
    skill_file = root / skill_name / "SKILL.md"
    root_r = root.resolve()
    file_r = skill_file.resolve()
    if not file_r.is_relative_to(root_r):
        raise HTTPException(status_code=400, detail=f"Invalid skill name: {skill_name!r}")
    return skill_file


def _format_skill_file(frontmatter: dict[str, Any], content: str) -> str:
    body = content.strip()
    yaml_frontmatter = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    return f"---\n{yaml_frontmatter}\n---\n\n{body}\n"


def _new_frontmatter(name: str, description: str) -> dict[str, Any]:
    return {"name": name, "description": description}


def _parse_skill_document(skill_file: Path) -> tuple[dict[str, Any], str]:
    content = skill_file.read_text(encoding="utf-8")
    if not content.startswith("---"):
        raise HTTPException(status_code=400, detail="Skill file has invalid frontmatter")
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise HTTPException(status_code=400, detail="Skill file has invalid frontmatter")
    try:
        frontmatter = yaml.safe_load(parts[1].strip()) or {}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail="Skill file has invalid frontmatter") from e
    if not isinstance(frontmatter, dict):
        raise HTTPException(status_code=400, detail="Skill file has invalid frontmatter")
    return frontmatter, parts[2].strip()


def _get_registry(user_id: str, workspace_id: str) -> Any:
    try:
        return get_skill_registry(user_id=user_id, workspace_id=workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _to_summary(
    skill: dict[str, Any] | Any,
    loaded_names: set[str],
    enabled: bool = True,
) -> SkillSummary:
    metadata = skill.get("metadata", {})
    scope, workspace_ids = _scope_response(enabled)
    return SkillSummary(
        name=skill["name"],
        description=skill.get("description", ""),
        scope=scope,
        workspace_ids=workspace_ids,
        enabled=enabled,
        is_loaded=skill["name"] in loaded_names,
        disable_model_invocation=metadata.get("disable_model_invocation", False) is True,
    )


def _to_detail(
    skill: dict[str, Any] | Any,
    loaded_names: set[str],
    enabled: bool = True,
) -> SkillDetail:
    summary = _to_summary(skill, loaded_names, enabled)
    frontmatter = {
        "name": skill["name"],
        "description": skill.get("description", ""),
        "metadata": skill.get("metadata", {}),
    }
    for field in ("license", "compatibility", "allowed_tools"):
        if field in skill:
            frontmatter[field] = skill[field]
    return SkillDetail(
        **summary.model_dump(),
        content=skill.get("content", ""),
        metadata=skill.get("metadata", {}),
        license=skill.get("license"),
        compatibility=skill.get("compatibility"),
        allowed_tools=skill.get("allowed_tools"),
        frontmatter=frontmatter,
    )


@router.get("", response_model=SkillListResponse)
async def list_skills(user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal", request: Request = None) -> SkillListResponse:
    user_id = resolve_user_id(request, user_id)
    _validate_user_id(user_id)
    _validate_workspace_id(workspace_id)
    registry = _get_registry(user_id, workspace_id)
    loaded_names = set(registry.get_loaded_skills())
    caps = _load_user_caps(user_id)

    summaries = []
    for skill in registry.get_all_skills():
        name = skill["name"]
        summary = _to_summary(skill, loaded_names, _resource_enabled(caps, "skills", name))
        summaries.append(summary)

    return SkillListResponse(skills=summaries)


@router.get("/{skill_name}", response_model=SkillDetail)
async def get_skill(
    skill_name: str,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
    request: Request = None,
) -> SkillDetail:
    user_id = resolve_user_id(request, user_id)
    _validate_user_id(user_id)
    _validate_workspace_id(workspace_id)
    _validate_skill_name(skill_name)
    registry = _get_registry(user_id, workspace_id)
    skill = registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    caps = _load_user_caps(user_id)
    enabled = _resource_enabled(caps, "skills", skill_name)
    return _to_detail(skill, set(registry.get_loaded_skills()), enabled=enabled)


@router.post("", response_model=SkillDetail)
async def create_skill(
    req: SkillCreateRequest,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
    request: Request = None,
) -> SkillDetail:
    user_id = resolve_user_id(request, user_id)
    _validate_user_id(user_id)
    _validate_workspace_id(workspace_id)
    _validate_skill_name(req.name)
    description = req.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="Description must not be empty")

    skill_file = _skill_file_path(user_id, req.name)
    if skill_file.exists():
        raise HTTPException(status_code=409, detail="Skill already exists")

    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(
        _format_skill_file(_new_frontmatter(req.name, description), req.content),
        encoding="utf-8",
    )
    _get_registry(user_id, workspace_id).reload()
    _reset_user_loops(user_id)

    skill = parse_skill_file(skill_file)
    if not skill:
        raise HTTPException(status_code=500, detail="Skill could not be loaded")
    enabled = _resource_enabled(_load_user_caps(user_id), "skills", req.name)
    return _to_detail(
        skill,
        set(_get_registry(user_id, workspace_id).get_loaded_skills()),
        enabled=enabled,
    )


@router.put("/{skill_name}", response_model=SkillDetail)
async def update_skill(
    skill_name: str,
    req: SkillUpdateRequest,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
    request: Request = None,
) -> SkillDetail:
    user_id = resolve_user_id(request, user_id)
    _validate_user_id(user_id)
    _validate_workspace_id(workspace_id)
    _validate_skill_name(skill_name)

    skill_file = _skill_file_path(user_id, skill_name)
    if not skill_file.exists():
        raise HTTPException(status_code=404, detail="Skill not found")

    frontmatter, current_content = _parse_skill_document(skill_file)
    frontmatter["name"] = skill_name
    if req.description is not None:
        d = req.description.strip()
        if not d:
            raise HTTPException(status_code=400, detail="Description must not be empty")
        frontmatter["description"] = d
    content = req.content if req.content is not None else current_content
    skill_file.write_text(_format_skill_file(frontmatter, content), encoding="utf-8")
    _get_registry(user_id, workspace_id).reload()
    _reset_user_loops(user_id)

    skill = parse_skill_file(skill_file)
    if not skill:
        raise HTTPException(status_code=500, detail="Skill could not be loaded")
    enabled = _resource_enabled(_load_user_caps(user_id), "skills", skill_name)
    return _to_detail(
        skill,
        set(_get_registry(user_id, workspace_id).get_loaded_skills()),
        enabled=enabled,
    )


@router.delete("/{skill_name}")
async def delete_skill(
    skill_name: str,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
    request: Request = None,
) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    _validate_user_id(user_id)
    _validate_workspace_id(workspace_id)
    _validate_skill_name(skill_name)

    skill_file = _skill_file_path(user_id, skill_name)
    skill_dir = skill_file.parent
    if not skill_file.exists() or not skill_dir.is_dir():
        raise HTTPException(status_code=404, detail="Skill not found")

    shutil.rmtree(skill_dir)
    _get_registry(user_id, workspace_id).reload()
    _reset_user_loops(user_id)
    return {"status": "deleted", "name": skill_name}


@router.patch("/{skill_name}/scope")
async def set_skill_scope(
    skill_name: str,
    body: dict[str, Any],
    user_id: str =  DEFAULT_USER_ID,
    request: Request = None,
) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    _validate_user_id(user_id)
    _validate_skill_name(skill_name)
    scope: ScopeKind = body.get("scope", "all")
    if scope not in ("all", "selected", "none"):
        raise HTTPException(status_code=400, detail="scope must be all, selected, or none")
    if scope == "selected":
        raise HTTPException(
            status_code=400,
            detail="workspace-selected scope is no longer supported; use 'all' or 'none'",
        )
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise HTTPException(status_code=400, detail="enabled must be a boolean")
        enabled = body["enabled"]
    else:
        enabled = scope != "none"
    _save_user_enabled(user_id, "skills", skill_name, enabled)
    _reset_user_loops(user_id)
    response_scope, wids = _scope_response(enabled)
    return {"name": skill_name, "scope": response_scope, "workspace_ids": wids}
