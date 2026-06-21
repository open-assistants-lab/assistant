"""Workspace management API for Flutter client."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.sdk.workspace_models import (
    Workspace,
    list_workspaces,
    load_workspace,
    save_workspace,
)
from src.sdk.workspace_models import (
    delete_workspace as _delete_ws,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class CreateWorkspaceRequest(BaseModel):
    name: str
    description: str = ""
    prompt: str = Field("", alias="instructions")
    model_override: str | None = None


class UpdateWorkspaceRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    prompt: str | None = Field(None, alias="instructions")
    model_override: str | None = None


@router.get("")
async def get_workspaces(user_id: str = "default_user") -> dict[str, Any]:
    workspaces = list_workspaces()
    return {
        "workspaces": [
            {
                "id": w.id,
                "name": w.name,
                "description": w.description,
                "prompt": w.prompt,
                "model_override": w.model_override,
            }
            for w in workspaces
        ]
    }


@router.post("")
async def create_workspace(req: CreateWorkspaceRequest) -> dict[str, Any]:
    ws = Workspace.from_name(req.name)
    ws.description = req.description
    ws.prompt = req.prompt
    if "model_override" in req.model_fields_set:
        ws.model_override = req.model_override

    from src.storage.paths import DataPaths
    dp = DataPaths(user_id="system")
    dp.user_files_dir()
    dp.user_skills_dir()
    dp.user_subagents_dir()

    save_workspace(ws)
    return {"id": ws.id, "name": ws.name, "model_override": ws.model_override}


@router.patch("/{workspace_id}")
async def update_workspace(workspace_id: str, req: UpdateWorkspaceRequest) -> dict[str, Any] | tuple[dict[str, Any], int]:
    ws = load_workspace(workspace_id)
    if ws is None:
        return {"error": "Workspace not found"}, 404

    if req.name is not None:
        ws.name = req.name
    if req.description is not None:
        ws.description = req.description
    if req.prompt is not None:
        ws.prompt = req.prompt
    if "model_override" in req.model_fields_set:
        ws.model_override = req.model_override

    save_workspace(ws)
    return ws.to_dict()


@router.delete("/{workspace_id}")
async def delete_workspace_endpoint(workspace_id: str, user_id: str = "default_user") -> dict[str, Any] | tuple[dict[str, Any], int]:
    ws = load_workspace(workspace_id)
    if ws is None or ws.id == "personal":
        return {"error": "Cannot delete"}, 400

    _delete_ws(ws.id)
    return {"status": "deleted"}
