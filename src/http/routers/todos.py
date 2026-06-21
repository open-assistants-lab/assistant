from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/todos", tags=["todos"])


@router.get("")
async def list_todos(user_id: str = "default_user") -> dict[str, Any]:
    """List all todos."""
    from src.sdk.tools_core.todos import todos_list

    result = await todos_list.ainvoke({"user_id": user_id})
    return {"todos": result}


@router.post("")
async def add_todo(
    content: str,
    priority: int | None = None,
    user_id: str = "default_user",
) -> dict[str, Any]:
    """Add a new todo."""
    from src.sdk.tools_core.todos import todos_add

    args: dict[str, Any] = {"user_id": user_id, "content": content}
    if priority is not None:
        args["priority"] = priority
    result = await todos_add.ainvoke(args)
    return {"result": str(result)}


@router.put("/{todo_id}")
async def update_todo(
    todo_id: str,
    content: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    user_id: str = "default_user",
) -> dict[str, Any]:
    """Update a todo."""
    from src.sdk.tools_core.todos import todos_update

    args: dict[str, Any] = {"user_id": user_id, "todo_id": todo_id}
    if content is not None:
        args["content"] = content
    if status is not None:
        args["status"] = status
    if priority is not None:
        args["priority"] = priority
    result = await todos_update.ainvoke(args)
    return {"result": str(result)}


@router.delete("/{todo_id}")
async def delete_todo(todo_id: str, user_id: str = "default_user") -> dict[str, Any]:
    """Delete a todo."""
    from src.sdk.tools_core.todos import todos_delete

    result = await todos_delete.ainvoke({"user_id": user_id, "todo_id": todo_id})
    return {"result": str(result)}
