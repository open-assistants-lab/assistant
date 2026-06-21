"""Subagent management API — globally available subagents."""

from __future__ import annotations

import json
from typing import Any

from agentprofile.models import AgentProfile
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from src.sdk.subagent_models import TaskStatus

router = APIRouter(prefix="/subagents", tags=["subagents"])


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


@router.get("")
async def list_subagents(
    user_id: str = Query("default_user"),) -> dict[str, Any]:
    from src.sdk.coordinator import get_coordinator

    coordinator = get_coordinator(user_id)
    defs = await coordinator.list_defs()

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
            }
            for d in defs
        ]
    }


@router.post("")
async def create_subagent(
    body: SubagentCreateRequest,
    user_id: str = Query("default_user"),) -> dict[str, Any]:
    from src.sdk.agent_validation import validate_agent_def
    from src.sdk.coordinator import get_coordinator

    coordinator = get_coordinator(user_id)
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

    validation_errors = validate_agent_def(agent_profile, user_id=user_id)
    if validation_errors:
        raise HTTPException(status_code=400, detail={"errors": validation_errors})

    await coordinator.create(agent_profile)
    return {"status": "created", "name": body.name}


@router.get("/jobs")
async def list_subagent_jobs(
    user_id: str = Query("default_user"),
    status: TaskStatus | None = Query(None),) -> dict[str, Any]:
    from src.sdk.work_queue import get_work_queue

    db = await get_work_queue(user_id)
    jobs = await db.check_progress(status=status)
    return {"jobs": [_serialize_job(job) for job in jobs]}


@router.get("/jobs/{job_id}")
async def get_subagent_job(
    job_id: str,
    user_id: str = Query("default_user"),) -> dict[str, Any]:
    from src.sdk.work_queue import get_work_queue

    db = await get_work_queue(user_id)
    row = await db.get_task(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": _serialize_job(row)}


@router.post("/jobs/{job_id}/instructions")
async def instruct_subagent_job(
    job_id: str,
    body: SubagentInstructionRequest,
    user_id: str = Query("default_user"),) -> dict[str, Any]:
    from src.sdk.coordinator import get_coordinator
    from src.sdk.work_queue import get_work_queue

    db = await get_work_queue(user_id)
    if await db.get_task(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    coordinator = get_coordinator(user_id)
    if not await coordinator.instruct(job_id, body.instruction):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "instruction_added", "job_id": job_id}


@router.post("/jobs/{job_id}/cancel")
async def cancel_subagent_job(
    job_id: str,
    user_id: str = Query("default_user"),) -> dict[str, Any]:
    from src.sdk.coordinator import get_coordinator
    from src.sdk.work_queue import get_work_queue

    db = await get_work_queue(user_id)
    if await db.get_task(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    coordinator = get_coordinator(user_id)
    if not await coordinator.cancel(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "cancel_requested", "job_id": job_id}


@router.patch("/{name}")
async def update_subagent(
    name: str,
    body: SubagentUpdateRequest,
    user_id: str = Query("default_user"),) -> dict[str, Any]:
    from src.sdk.agent_validation import validate_agent_def
    from src.sdk.coordinator import get_coordinator

    coordinator = get_coordinator(user_id)
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

    errors = validate_agent_def(candidate, user_id=user_id)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    updated = await coordinator.update(name, **update_data)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Subagent '{name}' not found.")
    return {"status": "updated", "subagent": updated.model_dump()}


@router.delete("/{name}")
async def delete_subagent(
    name: str,
    user_id: str = Query("default_user"),) -> dict[str, Any]:
    from src.sdk.coordinator import get_coordinator

    coordinator = get_coordinator(user_id)
    if not await coordinator.delete(name):
        raise HTTPException(status_code=404, detail=f"Subagent '{name}' not found.")
    return {"status": "deleted", "name": name}


@router.post("/{name}/start")
async def start_subagent(
    name: str,
    body: SubagentStartRequest,
    user_id: str = Query("default_user"),) -> dict[str, Any]:
    from src.sdk.coordinator import get_coordinator

    coordinator = get_coordinator(user_id)
    try:
        task_id = await coordinator.start(name, body.task, parent_id=body.parent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"job_id": task_id, "status": "pending", "subagent": name}


@router.patch("/{name}/scope")
async def set_subagent_scope(
    name: str,
    body: dict[str, Any],
    user_id: str = Query("default_user"),
) -> dict[str, Any]:
    return {"name": name, "scope": "all", "workspace_ids": []}
