"""Subagent management API for Flutter client."""

from __future__ import annotations

import json
from typing import Any

from agentprofile.models import AgentProfile
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError

from src.http.auth import resolve_user_id
from src.sdk.capabilities import load_user_capabilities, resource_enabled, save_user_capabilities
from src.sdk.subagent_models import TaskStatus
from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(prefix="/subagents", tags=["subagents"])

ScopeKind = str


class SubagentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = ""
    model: str | None = None
    provider_options: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    tools: list[str] | None = None
    system_prompt: str | None = None
    max_llm_calls: int = 50
    cost_limit_usd: float = 1.0
    timeout_seconds: int = 300
    output_schema: dict[str, Any] | None = None
    handoff_instructions: str | None = None
    tags: list[str] = Field(default_factory=list)


class SubagentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = None
    model: str | None = None
    provider_options: dict[str, Any] | None = None
    skills: list[str] | None = None
    tools: list[str] | None = None
    system_prompt: str | None = None
    max_llm_calls: int | None = None
    cost_limit_usd: float | None = None
    timeout_seconds: int | None = None
    output_schema: dict[str, Any] | None = None
    handoff_instructions: str | None = None


class SubagentStartRequest(BaseModel):
    task: str = Field(..., min_length=1)
    parent_id: str | None = None


class SubagentInstructionRequest(BaseModel):
    instruction: str = Field(..., min_length=1)


def _parse_json_field(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _serialize_job(row: dict[str, Any]) -> dict[str, Any]:
    job = dict(row)
    job["progress"] = _parse_json_field(job.get("progress")) or {}
    job["result"] = _parse_json_field(job.get("result"))
    job["instructions"] = _parse_json_field(job.get("instructions")) or []
    return job


def _validate_context_ids(user_id: str, workspace_id: str) -> None:
    from src.storage.paths import get_paths

    try:
        get_paths(user_id, workspace_id=workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


def _load_user_caps(user_id: str) -> dict[str, Any]:
    return load_user_capabilities(user_id)


def _save_user_enabled(user_id: str, section: str, name: str, enabled: bool) -> None:
    caps = load_user_capabilities(user_id)
    caps.setdefault(section, {})[name] = enabled
    save_user_capabilities(user_id, caps)


def _scope_response(enabled: bool) -> tuple[ScopeKind, list[str]]:
    return ("all" if enabled else "none", [])


def _resource_enabled(caps: dict[str, Any], section: str, name: str) -> bool:
    return resource_enabled(caps, section, name)


def _reset_user_loops(user_id: str) -> None:
    from src.sdk.runner import reset_user_sdk_loops

    reset_user_sdk_loops(user_id)


@router.get("")
async def list_subagents(
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),
    request: Request = None,) -> dict[str, Any]:
    if request is not None:
        user_id = resolve_user_id(request, user_id)
    from src.sdk.coordinator import get_coordinator

    _validate_context_ids(user_id, workspace_id)
    coordinator = get_coordinator(user_id, workspace_id)
    scoped_defs = await coordinator.list_defs_with_scope()
    caps = _load_user_caps(user_id)

    return {
        "agents": [
            {
                "name": d.name,
                "description": d.description or "",
                "model": d.model,
                "tools": d.tools,
                "skills": d.skills,
                "system_prompt": d.system_prompt,
                "max_llm_calls": d.max_llm_calls,
                "cost_limit_usd": d.cost_limit_usd,
                "timeout_seconds": d.timeout_seconds,
                "provider_options": d.provider_options,
                "output_schema": d.output_schema_def,
                "handoff_instructions": d.handoff_instructions,
                "enabled": enabled,
                "scope": scp,
                "workspace_ids": wids,
            }
            for d, _ignored_scope in scoped_defs
            for enabled in [_resource_enabled(caps, "subagents", d.name)]
            for scp, wids in [_scope_response(enabled)]
        ]
    }


@router.post("")
async def create_subagent(
    body: SubagentCreateRequest,
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),) -> dict[str, Any]:
    from src.sdk.agent_validation import validate_agent_def
    from src.sdk.coordinator import get_coordinator

    _validate_context_ids(user_id, workspace_id)
    coordinator = get_coordinator(user_id, workspace_id)
    if coordinator.load_def(body.name) is not None:
        raise HTTPException(status_code=400, detail=f"Subagent '{body.name}' already exists.")

    try:
        data = body.model_dump()
        agent_profile = AgentProfile(
            name=data["name"],
            description=data.get("description", ""),
            model=data.get("model") or "",
            tools=data.get("tools") or [],
            system_prompt=data.get("system_prompt") or "",
            skills=data.get("skills", []),
            max_llm_calls=data.get("max_llm_calls", 50),
            cost_limit_usd=data.get("cost_limit_usd", 1.0),
            timeout_seconds=data.get("timeout_seconds", 300),
            provider_options=data.get("provider_options", {}),
            handoff_instructions=data.get("handoff_instructions"),
            tags=data.get("tags", []),
        )
        output_schema = data.get("output_schema")
        if output_schema:
            agent_profile.output_schema_def = output_schema
    except ValidationError as e:
        errors: list[dict[str, Any]] = [dict(err) for err in e.errors()]
        for err in errors:
            if "ctx" in err and "error" in err["ctx"]:
                err["ctx"]["error"] = str(err["ctx"]["error"])
        raise HTTPException(status_code=422, detail=errors) from e

    validation_errors = validate_agent_def(agent_profile, user_id=user_id, workspace_id=workspace_id)
    if validation_errors:
        raise HTTPException(status_code=400, detail={"errors": validation_errors})

    await coordinator.create(agent_profile)
    return {"status": "created", "name": body.name, "workspace_id": workspace_id}


@router.get("/jobs")
async def list_subagent_jobs(
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),
    status: TaskStatus | None = Query(None),) -> dict[str, Any]:
    from src.sdk.work_queue import get_work_queue

    _validate_context_ids(user_id, workspace_id)
    db = await get_work_queue(user_id, workspace_id)
    jobs = await db.check_progress(status=status)
    return {"jobs": [_serialize_job(job) for job in jobs]}


@router.get("/jobs/{job_id}")
async def get_subagent_job(
    job_id: str,
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),) -> dict[str, Any]:
    from src.sdk.work_queue import get_work_queue

    _validate_context_ids(user_id, workspace_id)
    db = await get_work_queue(user_id, workspace_id)
    row = await db.get_task(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": _serialize_job(row)}


@router.post("/jobs/{job_id}/instructions")
async def instruct_subagent_job(
    job_id: str,
    body: SubagentInstructionRequest,
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),) -> dict[str, Any]:
    from src.sdk.coordinator import get_coordinator
    from src.sdk.work_queue import get_work_queue

    _validate_context_ids(user_id, workspace_id)
    db = await get_work_queue(user_id, workspace_id)
    if await db.get_task(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    coordinator = get_coordinator(user_id, workspace_id)
    if not await coordinator.instruct(job_id, body.instruction):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "instruction_added", "job_id": job_id}


@router.post("/jobs/{job_id}/cancel")
async def cancel_subagent_job(
    job_id: str,
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),) -> dict[str, Any]:
    from src.sdk.coordinator import get_coordinator
    from src.sdk.work_queue import get_work_queue

    _validate_context_ids(user_id, workspace_id)
    db = await get_work_queue(user_id, workspace_id)
    if await db.get_task(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    coordinator = get_coordinator(user_id, workspace_id)
    if not await coordinator.cancel(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "cancel_requested", "job_id": job_id}


@router.patch("/{name}")
async def update_subagent(
    name: str,
    body: SubagentUpdateRequest,
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),) -> dict[str, Any]:
    from src.sdk.agent_validation import validate_agent_def
    from src.sdk.coordinator import get_coordinator

    _validate_context_ids(user_id, workspace_id)
    coordinator = get_coordinator(user_id, workspace_id)
    update_data = body.model_dump(exclude_unset=True)
    update_data.pop("name", None)

    current = coordinator.load_def(name)
    if current is None:
        raise HTTPException(status_code=404, detail=f"Subagent '{name}' not found.")

    candidate_data = current.model_dump()
    candidate_data.update({k: v for k, v in update_data.items() if v is not None})
    try:
        candidate = AgentProfile(**candidate_data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e

    errors = validate_agent_def(candidate, user_id=user_id, workspace_id=workspace_id)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    updated = await coordinator.update(name, **update_data)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Subagent '{name}' not found.")
    return {"status": "updated", "subagent": updated.model_dump()}


@router.delete("/{name}")
async def delete_subagent(
    name: str,
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),) -> dict[str, Any]:
    from src.sdk.coordinator import get_coordinator

    _validate_context_ids(user_id, workspace_id)
    coordinator = get_coordinator(user_id, workspace_id)
    if not await coordinator.delete(name):
        raise HTTPException(status_code=404, detail=f"Subagent '{name}' not found.")
    return {"status": "deleted", "name": name, "workspace_id": workspace_id}


@router.post("/{name}/start")
async def start_subagent(
    name: str,
    body: SubagentStartRequest,
    user_id: str = Query(DEFAULT_USER_ID),
    workspace_id: str = Query("personal"),) -> dict[str, Any]:
    from src.sdk.coordinator import get_coordinator

    _validate_context_ids(user_id, workspace_id)
    coordinator = get_coordinator(user_id, workspace_id)
    try:
        task_id = await coordinator.start(name, body.task, parent_id=body.parent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"job_id": task_id, "status": "pending", "subagent": name}


@router.patch("/{name}/scope")
async def set_subagent_scope(
    name: str,
    body: dict[str, Any],
    user_id: str = Query(DEFAULT_USER_ID),
) -> dict[str, Any]:
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
    _save_user_enabled(user_id, "subagents", name, enabled)
    _reset_user_loops(user_id)
    response_scope, wids = _scope_response(enabled)
    return {"name": name, "scope": response_scope, "workspace_ids": wids}
