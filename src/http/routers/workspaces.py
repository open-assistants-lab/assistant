# mypy: disable-error-code="assignment"
"""Workspace management API for Flutter client."""
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from src.http.auth import enforce_user_id
from src.sdk.runner import reset_user_sdk_loops
from src.sdk.workspace_models import (
    Workspace,
    list_workspaces,
    load_workspace,
    save_workspace,
)
from src.sdk.workspace_models import (
    delete_workspace as _delete_ws,
)
from src.storage.messages import get_message_store

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
async def get_workspaces(user_id: str = "default_user", request: Request = None) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    workspaces = list_workspaces(user_id=user_id)
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
async def create_workspace(
    req: CreateWorkspaceRequest, user_id: str = "default_user", request: Request = None
) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    ws = Workspace.from_name(req.name)
    ws.description = req.description
    ws.prompt = req.prompt
    if "model_override" in req.model_fields_set:
        ws.model_override = req.model_override

    from src.storage.paths import DataPaths
    dp = DataPaths(user_id=user_id, workspace_id=ws.id)
    dp.workspace_files_dir()
    dp.workspace_memory_dir()
    dp.workspace_subagents_dir()
    dp.workspace_skills_dir()

    save_workspace(ws, user_id=user_id)
    return {"id": ws.id, "name": ws.name, "model_override": ws.model_override}


@router.patch("/{workspace_id}")
async def update_workspace(
    workspace_id: str, req: UpdateWorkspaceRequest, user_id: str = "default_user", request: Request = None
) -> dict[str, Any] | tuple[dict[str, Any], int]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    ws = load_workspace(workspace_id, user_id=user_id)
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

    save_workspace(ws, user_id=user_id)
    reset_user_sdk_loops(user_id, reason=f"workspace_updated:{workspace_id}")
    return ws.to_dict()


@router.delete("/{workspace_id}")
async def delete_workspace_endpoint(workspace_id: str, user_id: str = "default_user", request: Request = None) -> dict[str, Any] | tuple[dict[str, Any], int]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    ws = load_workspace(workspace_id, user_id=user_id)
    if ws is None or ws.id == "personal":
        return {"error": "Cannot delete"}, 400

    # Audit E7: purge the workspace's messages (and legacy-{ws}-* imported
    # sessions) and report the real deleted count instead of a hardcoded 0.
    store = get_message_store(user_id)
    count = store.delete_messages_for_workspace(ws.id)
    for session in store.get_sessions():
        if session["session_id"].startswith(f"legacy-{ws.id}-"):
            count += store.delete_session(session["session_id"])

    _delete_ws(ws.id, user_id=user_id)
    reset_user_sdk_loops(user_id, reason=f"workspace_deleted:{workspace_id}")
    return {"status": "deleted", "messages_deleted": count}
