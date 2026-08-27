# mypy: disable-error-code="assignment"
import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import StreamingResponse

from src.http.auth import enforce_user_id
from src.storage.paths import DEFAULT_USER_ID, get_paths

router = APIRouter(tags=["workspace"])


@router.get("/workspace/json")
async def list_workspace_json(user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal", request: Request = None) -> dict[str, Any]:
    """List files in workspace as structured JSON."""
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    from src.storage.paths import DataPaths
    paths = DataPaths(user_id=user_id, workspace_id=workspace_id)
    workspace_dir = paths.workspace_files_dir()
    items = []
    if workspace_dir.exists():
        for item in sorted(workspace_dir.iterdir()):
            is_dir = item.is_dir()
            stat = item.stat()
            items.append({
                "name": item.name,
                "is_dir": is_dir,
                "size": stat.st_size if not is_dir else 0,
                "modified": stat.st_mtime,
            })
    return {"files": items, "path": str(workspace_dir)}


@router.get("/workspace/read/{path:path}")
async def read_workspace_file(path: str, user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal", request: Request = None) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    from src.http.workspace_cache import get_file_cache
    from src.sdk.tools_core.filesystem import files_read

    result = files_read.invoke({"path": path, "user_id": user_id, "workspace_id": workspace_id})

    file_cache = get_file_cache(user_id, workspace_id)
    workspace_root = get_paths(user_id, workspace_id=workspace_id).workspace_files_dir()
    workspace_path = workspace_root / path
    server_modified = str(workspace_path.stat().st_mtime) if workspace_path.exists() else ""

    file_cache.update_sync(path, server_modified)

    return {"response": str(result), "path": path}


@router.get("/workspace/{path:path}")
async def list_workspace_files(path: str = "", user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal", request: Request = None) -> dict[str, Any]:
    """List files in workspace."""
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    from src.sdk.tools_core.filesystem import files_list

    result = files_list.invoke({"path": path, "user_id": user_id, "workspace_id": workspace_id})
    return {"response": str(result)}


@router.post("/workspace/{path:path}")
async def write_workspace_file(
    path: str,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
    req: dict[str, Any] | None = Body(default=None),
    request: Request = None,
) -> dict[str, Any]:
    """Write file to workspace."""
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    if req is None:
        return {"error": "content is required"}

    content = req.get("content", "")

    from src.sdk.tools_core.filesystem import files_write

    result = files_write.invoke(
        {"path": path, "content": content, "user_id": user_id, "workspace_id": workspace_id}
    )
    return {"response": str(result)}


@router.delete("/workspace/{path:path}")
async def delete_workspace_file(path: str, user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal", request: Request = None) -> dict[str, Any]:
    """Delete file from workspace."""
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    from src.sdk.tools_core.filesystem import files_delete

    result = files_delete.invoke({"path": path, "user_id": user_id, "workspace_id": workspace_id})
    return {"response": str(result)}


@router.get("/sync/status")
async def get_sync_status(user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal", request: Request = None) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    from src.http.workspace_cache import get_file_cache

    cache = get_file_cache(user_id, workspace_id)
    return {"status": cache.get_all()}


@router.post("/sync/pin/{path:path}")
async def pin_file(path: str, user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal", request: Request = None) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    from src.http.workspace_cache import get_file_cache

    cache = get_file_cache(user_id, workspace_id)
    cache.mark_pinned(path)
    return {"status": "pinned", "path": path}


@router.delete("/sync/pin/{path:path}")
async def unpin_file(path: str, user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal", request: Request = None) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    from src.http.workspace_cache import get_file_cache

    cache = get_file_cache(user_id, workspace_id)
    cache.mark_cloud_only(path)
    return {"status": "cloud_only", "path": path}


@router.post("/sync/download/{path:path}")
async def mark_downloaded(path: str, user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal", request: Request = None) -> dict[str, Any]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    from src.http.workspace_cache import get_file_cache

    cache = get_file_cache(user_id, workspace_id)
    cache.mark_downloaded(path)
    return {"status": "downloaded", "path": path}


@router.get("/sync/stream")
async def sync_stream(user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal", request: Request = None) -> StreamingResponse:
    """SSE stream for real-time file change notifications."""
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))

    async def event_generator() -> AsyncGenerator[str, None]:
        paths = get_paths(user_id, workspace_id=workspace_id)
        workspace_path = paths.workspace_files_dir()

        skills_path = get_paths(user_id).user_skills_dir()
        subagents_path = get_paths(user_id).subagents_dir()

        last_state: dict[str, Any] = {
            "workspace": {},
            "skills": {},
            "subagents": {},
        }

        while True:
            try:
                current_state: dict[str, Any] = {
                    "workspace": {},
                    "skills": {},
                    "subagents": {},
                }

                if workspace_path.exists():
                    for f in workspace_path.rglob("*"):
                        if f.is_file():
                            rel_path = str(f.relative_to(workspace_path))
                            mtime = str(f.stat().st_mtime)
                            current_state["workspace"][rel_path] = mtime

                if skills_path.exists():
                    for f in skills_path.glob("*/SKILL.md"):
                        skill_name = f.parent.name
                        current_state["skills"][skill_name] = str(f.stat().st_mtime)

                if subagents_path.exists():
                    for f in subagents_path.glob("*/config.yaml"):
                        agent_name = f.parent.name
                        current_state["subagents"][agent_name] = str(f.stat().st_mtime)

                for category in ["workspace", "skills", "subagents"]:
                    new_items = set(current_state[category].keys()) - set(
                        last_state[category].keys()
                    )
                    changed_items = []

                    for path_item, mtime in current_state[category].items():
                        if path_item not in last_state[category]:
                            changed_items.append(path_item)
                        elif last_state[category][path_item] != mtime:
                            changed_items.append(path_item)

                    if changed_items or new_items:
                        all_changed = list(set(changed_items) | set(new_items))
                        for item in all_changed:
                            data = {
                                "type": f"{category}_changed",
                                "category": category,
                                "path": item,
                                "action": "created" if item in new_items else "modified",
                                "timestamp": datetime.now(UTC).isoformat(),
                            }
                            yield f"data: {json.dumps(data)}\n\n"

                last_state = current_state
                await asyncio.sleep(3)

            except asyncio.CancelledError:
                break
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                await asyncio.sleep(5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
