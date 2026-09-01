from typing import Any

from fastapi import APIRouter, Request

from src.http.auth import resolve_user_id
from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(prefix="/todos", tags=["todos"])


@router.get("")
async def list_todos(request: Request, user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    user_id = resolve_user_id(request, user_id)
    """List all todos."""
    from src.sdk.tools_core.todos import todos_list

    result = todos_list.invoke({"user_id": user_id})
    return {"todos": result}


@router.post("")
async def add_todo(
    content: str,
    priority: int | None = None,
    user_id: str =  DEFAULT_USER_ID,
    request: Request = None,
) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    """Add a new todo."""
    from src.sdk.tools_core.todos import todos_add

    args: dict[str, Any] = {"user_id": user_id, "content": content}
    if priority is not None:
        args["priority"] = priority
    result = todos_add.invoke(args)
    return {"result": str(result)}


@router.put("/{todo_id}")
async def update_todo(
    todo_id: str,
    content: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    user_id: str =  DEFAULT_USER_ID,
    request: Request = None,
) -> dict[str, Any]:
    user_id = resolve_user_id(request, user_id)
    """Update a todo."""
    from src.sdk.tools_core.todos import todos_update

    args: dict[str, Any] = {"user_id": user_id, "todo_id": todo_id}
    if content is not None:
        args["content"] = content
    if status is not None:
        args["status"] = status
    if priority is not None:
        args["priority"] = priority
    result = todos_update.invoke(args)
    return {"result": str(result)}


@router.delete("/{todo_id}")
async def delete_todo(todo_id: str, user_id: str =  DEFAULT_USER_ID) -> dict[str, Any]:
    """Delete a todo."""
    from src.sdk.tools_core.todos import todos_delete

    result = todos_delete.invoke({"user_id": user_id, "todo_id": todo_id})
    return {"result": str(result)}
